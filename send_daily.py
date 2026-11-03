#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日 AI 新闻简报发送器（仅用 Python 标准库，零第三方依赖）

功能：
  1. 从多个 RSS/Atom 源抓取最新 AI 新闻
  2. （可选）调用 DeepSeek API 生成中文 AI 摘要
  3. 通过 SMTP 把简报作为 HTML 邮件发送到指定邮箱

用法（在项目目录下执行）：
  python send_daily.py             # 抓取 + 摘要 + 发邮件
  python send_daily.py --dry-run   # 只抓取并生成本地 preview.html，不发送
  python send_daily.py --test      # 发送一封测试邮件，验证邮箱配置
"""

import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parsedate_to_datetime

# 让打印日志在任何环境下（含计划任务的无控制台场景）都不因编码问题崩溃
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("AI_NEWS_CONFIG", os.path.join(BASE_DIR, "config.json"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Newsletter/1.0"

# 常见邮箱的 SMTP 配置（按发件人域名自动识别）
SMTP_PRESETS = {
    "qq.com":      {"host": "smtp.qq.com",        "port": 465, "ssl": True},
    "163.com":     {"host": "smtp.163.com",       "port": 465, "ssl": True},
    "126.com":     {"host": "smtp.126.com",       "port": 465, "ssl": True},
    "gmail.com":   {"host": "smtp.gmail.com",     "port": 465, "ssl": True},
    "outlook.com": {"host": "smtp.office365.com", "port": 587, "ssl": False},
    "hotmail.com": {"host": "smtp.office365.com", "port": 587, "ssl": False},
    "live.com":    {"host": "smtp.office365.com", "port": 587, "ssl": False},
    "foxmail.com": {"host": "smtp.qq.com",        "port": 465, "ssl": True},
}


DEFAULT_FEEDS = [
    "https://www.qbitai.com/feed",
    "https://www.infoq.cn/feed",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://blog.google/technology/ai/rss/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://export.arxiv.org/rss/cs.AI",
]


def load_config():
    cfg = {"email": {}, "ai": {}, "news": {}}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = data
        except Exception as e:
            print("  [警告] 读取 config.json 失败，改用环境变量/默认值：" + str(e))

    cfg.setdefault("email", {})
    cfg.setdefault("ai", {})
    cfg.setdefault("news", {})

    # 环境变量优先（供 GitHub Actions 的 Secrets 使用）
    env_map = {
        "EMAIL_SENDER": ("email", "sender"),
        "EMAIL_AUTH_CODE": ("email", "authorizationCode"),
        "EMAIL_RECIPIENT": ("email", "recipient"),
        "EMAIL_SUBJECT_PREFIX": ("email", "subjectPrefix"),
        "DEEPSEEK_API_KEY": ("ai", "apiKey"),
        "DEEPSEEK_BASE_URL": ("ai", "baseUrl"),
        "DEEPSEEK_MODEL": ("ai", "model"),
    }
    for env_name, (section, key) in env_map.items():
        val = os.environ.get(env_name)
        if val:
            cfg[section][key] = val

    # 订阅源兜底：config.json 没配就用默认源
    if not cfg["news"].get("feeds"):
        cfg["news"]["feeds"] = list(DEFAULT_FEEDS)
    return cfg


# ---------- 网络抓取 ----------

def http_get(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
        return resp.read()


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def child_text(node, tag):
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == tag:
            return strip_html("".join(child.itertext()))
    return ""


def parse_feed(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError("XML 解析失败: " + str(e))
    kind = root.tag.rsplit("}", 1)[-1].lower()
    items = []

    if kind == "feed":  # Atom
        ns = "http://www.w3.org/2005/Atom"
        entries = root.findall("{%s}entry" % ns) or root.findall("entry")
        for entry in entries:
            link = ""
            links = entry.findall("{%s}link" % ns) or entry.findall("link")
            for l in links:
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href") or ""
                    break
            items.append({
                "title": child_text(entry, "title"),
                "link": link,
                "summary": child_text(entry, "summary") or child_text(entry, "content"),
                "date": child_text(entry, "updated") or child_text(entry, "published"),
            })
    elif kind == "rss":  # RSS 2.0
        channel = root.find("channel")
        container = channel if channel is not None else root
        for item in container.findall("item"):
            items.append({
                "title": child_text(item, "title"),
                "link": child_text(item, "link"),
                "summary": child_text(item, "description"),
                "date": child_text(item, "pubDate"),
            })
    else:
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry"):
                items.append({
                    "title": child_text(node, "title"),
                    "link": child_text(node, "link"),
                    "summary": child_text(node, "description") or child_text(node, "summary"),
                    "date": child_text(node, "pubDate") or child_text(node, "updated"),
                })
    return items


def parse_date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _sort_key(it):
    d = parse_date(it.get("date"))
    if d is None:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def fetch_news(feeds, max_per_feed, max_total):
    seen = set()
    items = []
    for url in feeds:
        url = (url or "").strip()
        if not url:
            continue
        try:
            raw = http_get(url)
            parsed = parse_feed(raw)
        except Exception as e:
            print("  [跳过] %s 抓取失败: %s" % (url, e))
            continue
        count = 0
        for it in parsed:
            title = (it.get("title") or "").strip()
            link = (it.get("link") or "").strip()
            if not title:
                continue
            key = link or title
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "link": link,
                "summary": (it.get("summary") or "").strip(),
                "date": (it.get("date") or "").strip(),
                "source": url,
            })
            count += 1
            if count >= max_per_feed:
                break
        print("  - %s -> %d 条" % (url, count))
    items.sort(key=_sort_key, reverse=True)
    return items[:max_total]


# ---------- AI 摘要 ----------

def call_deepseek(config, prompt):
    ai = config.get("ai", {})
    api_key = (ai.get("apiKey") or "").strip()
    if not api_key:
        return None
    base_url = (ai.get("baseUrl") or "https://api.deepseek.com").rstrip("/")
    model = ai.get("model") or "deepseek-chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是资深科技媒体编辑，擅长把大量新闻浓缩成结构清晰、重点突出、可读性强的中文简报。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": False,
    }
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    return (content or "").strip() or None


def build_prompt(items, date_str):
    lines = []
    for i, it in enumerate(items, 1):
        summary = (it.get("summary") or "").strip()
        summary = summary[:220] if summary else ""
        line = "%d. %s" % (i, it["title"])
        if summary:
            line += " — " + summary
        lines.append(line)
    news_text = "\n".join(lines)
    return (
        "今天是 %s，以下是今天抓取到的 AI 相关新闻（共 %d 条）：\n\n%s\n\n"
        "请据此生成一份中文《AI 日报》，要求：\n"
        "1. 开头用 1~2 句话总结今天 AI 领域的总体动态或趋势；\n"
        "2. 挑出你认为最重要的 5~10 条新闻，用小标题分点概括，每条 2~3 句中文，并给出对应链接；\n"
        "3. 其余新闻归入「其他值得关注」小节，只列标题；\n"
        "4. 用 Markdown 输出（## 小标题、**加粗**、- 列表、[标题](链接)）。\n"
    ) % (date_str, len(items), news_text)


def markdown_to_html(md):
    def inline(s):
        s = html.escape(s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2" style="color:#2563eb;">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"\`([^\`]+)\`", r'<code style="background:#f1f5f9;padding:1px 4px;border-radius:4px;">\1</code>', s)
        return s

    out = []
    in_list = None
    for line in md.strip().splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            if in_list:
                out.append("</%s>" % in_list)
                in_list = None
            level = min(len(m.group(1)), 4)
            sizes = {1: "22px", 2: "18px", 3: "16px", 4: "15px"}
            out.append('<h%d style="color:#0f172a;margin:18px 0 8px;font-size:%s;line-height:1.4;">%s</h%d>' % (level, sizes[level], inline(m.group(2)), level))
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)$", s)
        if m:
            if in_list != "ul":
                if in_list:
                    out.append("</%s>" % in_list)
                out.append('<ul style="margin:6px 0 12px;padding-left:20px;">')
                in_list = "ul"
            out.append('<li style="margin:4px 0;line-height:1.6;">%s</li>' % inline(m.group(1)))
            continue
        m = re.match(r"^\s*\d+[.)]\s+(.*)$", s)
        if m:
            if in_list != "ol":
                if in_list:
                    out.append("</%s>" % in_list)
                out.append('<ol style="margin:6px 0 12px;padding-left:20px;">')
                in_list = "ol"
            out.append('<li style="margin:4px 0;line-height:1.6;">%s</li>' % inline(m.group(1)))
            continue
        if not s.strip():
            continue
        if in_list:
            out.append("</%s>" % in_list)
            in_list = None
        out.append('<p style="margin:8px 0;line-height:1.7;color:#334155;">%s</p>' % inline(s))
    if in_list:
        out.append("</%s>" % in_list)
    return "\n".join(out)


# ---------- 邮件 ----------

def render_email_html(date_str, summary_html, items):
    def esc(s):
        return html.escape(s or "")

    news_rows = []
    for it in items:
        title = esc(it["title"])
        link = esc(it["link"])
        summary = esc(it["summary"]) if it["summary"] else ""
        if summary:
            summary = summary[:160] + ("…" if len(summary) > 160 else "")
        title_html = ('<a href="%s" style="color:#2563eb;text-decoration:none;font-weight:600;">%s</a>' % (link, title)) if link else ("<strong>%s</strong>" % title)
        news_rows.append(
            '<li style="margin:10px 0;padding-bottom:10px;border-bottom:1px solid #eef2f7;">'
            '<div style="line-height:1.5;">%s</div>'
            '<div style="color:#64748b;font-size:13px;margin-top:3px;line-height:1.5;">%s</div>'
            '</li>' % (title_html, summary)
        )

    if summary_html:
        summary_block = (
            '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:0 0 18px;">'
            + summary_html + "</div>"
        )
    else:
        summary_block = (
            '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;margin:0 0 18px;color:#92400e;font-size:13px;">'
            "当前未配置 AI 摘要（config.json 里 ai.apiKey 为空），以下为新闻原文列表。填写 DeepSeek API Key 后即可自动生成精炼日报。"
            "</div>"
        )

    html_body = """<!DOCTYPE html>
<html lang="zh-CN">
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:20px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">
        <tr>
          <td style="background:linear-gradient(135deg,#0f172a,#1d4ed8);padding:26px 28px;">
            <div style="color:#ffffff;font-size:22px;font-weight:700;">AI 日报</div>
            <div style="color:#cbd5e1;font-size:13px;margin-top:6px;">%s · 共 %d 条资讯</div>
          </td>
        </tr>
        <tr><td style="padding:22px 28px;">
          %s
          <h2 style="color:#0f172a;font-size:17px;margin:0 0 6px;">全部新闻</h2>
          <ul style="list-style:none;margin:0;padding:0;">%s</ul>
        </td></tr>
        <tr><td style="background:#f8fafc;padding:16px 28px;color:#94a3b8;font-size:12px;line-height:1.6;">
          本邮件由 AI 简报发送器自动生成。如需调整订阅源、发送时间或停用，请查看项目 README.md。
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>""" % (esc(date_str), len(items), summary_block, "".join(news_rows))
    return html_body


def send_email(config, subject, html_body):
    email_cfg = config.get("email", {})
    sender = (email_cfg.get("sender") or "").strip()
    recipient = (email_cfg.get("recipient") or "").strip()
    auth_code = email_cfg.get("authorizationCode") or ""
    if not sender or "@" not in sender:
        sys.exit("config.json 的 email.sender 未正确填写")
    if not recipient:
        recipient = sender
    if not auth_code:
        sys.exit("config.json 的 email.authorizationCode 未填写（这是 SMTP 授权码，不是邮箱登录密码）")

    domain = sender.split("@")[-1].lower()
    preset = dict(SMTP_PRESETS.get(domain) or {})
    preset.update(email_cfg.get("smtp") or {})
    host = preset.get("host")
    if not host:
        sys.exit("未内置 %s 的 SMTP 配置，请在 config.json 的 email.smtp 里手动填写 host/port/ssl。" % domain)

    port = int(preset.get("port", 465))
    use_ssl = bool(preset.get("ssl", port == 465))

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("AI 日报", "utf-8")), sender))
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    try:
        server.login(sender, auth_code)
        server.sendmail(sender, [recipient], msg.as_string())
        print("  已通过 %s:%d 发送给 %s" % (host, port, recipient))
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ---------- 主流程 ----------

def main():
    dry_run = "--dry-run" in sys.argv
    test = "--test" in sys.argv
    config = load_config()

    if test:
        send_email(config, "AI 日报 · 测试邮件", "<p style='font-family:sans-serif;'>这是一封测试邮件，说明你的邮箱（SMTP）配置已经正确。</p>")
        print("测试邮件发送完成。")
        return

    today = datetime.now().strftime("%Y年%m月%d日")
    news_cfg = config.get("news", {})
    feeds = news_cfg.get("feeds", [])
    max_per = int(news_cfg.get("maxItemsPerFeed", 8))
    max_total = int(news_cfg.get("maxTotalItems", 40))

    print("[1/3] 抓取 %d 个新闻源 …" % len(feeds))
    items = fetch_news(feeds, max_per, max_total)
    if not items:
        print("没有抓到任何新闻，本次退出。")
        return
    print("  共 %d 条。" % len(items))

    print("[2/3] 生成 AI 摘要 …")
    summary_html = None
    try:
        summary_md = call_deepseek(config, build_prompt(items, today))
        if summary_md:
            summary_html = markdown_to_html(summary_md)
            print("  AI 摘要已生成。")
        else:
            print("  未配置 apiKey，跳过 AI 摘要，仅推送新闻列表。")
    except Exception as e:
        print("  [警告] AI 摘要失败，改用新闻列表：%s" % e)

    html_body = render_email_html(today, summary_html, items)

    if dry_run:
        out_path = os.path.join(BASE_DIR, "preview.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print("[dry-run] 未发送邮件，预览已写入：" + out_path)
        return

    print("[3/3] 发送邮件 …")
    prefix = config["email"].get("subjectPrefix") or "AI 日报"
    send_email(config, prefix + " · " + today, html_body)
    print("完成，邮件已发送。")


if __name__ == "__main__":
    main()
