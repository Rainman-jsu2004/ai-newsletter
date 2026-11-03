# AI 日报 · 每日新闻简报发送器

每天自动抓取最新 AI 新闻，可选调用 DeepSeek 生成精炼摘要，然后以 HTML 邮件发送到你的邮箱。
**仅用 Python 标准库，无需安装任何第三方包。**

## 目录结构

```
ai-newsletter/
├── send_daily.py   主程序：抓取 + 摘要 + 发邮件
├── config.json   你的配置（含密钥，仅本地，不上传）
├── config.example.json   配置模板（无密钥，会上传）
├── schedule.ps1   Windows 本地定时任务
├── README.md   说明文档
├── .gitignore   忽略 config.json 等敏感文件
└── .github/workflows/
    └── daily.yml   GitHub 云端定时工作流
```

## 快速开始（3 步）

### 第 1 步：填写配置

用任意文本编辑器打开 `config.json`，填写：

| 字段 | 说明 |
|---|---|
| `email.sender` | 发件邮箱地址（如 `123456@qq.com`） |
| `email.authorizationCode` | 该邮箱的 **SMTP 授权码**（不是登录密码，见下文） |
| `email.recipient` | 接收邮箱（可与发件人相同） |
| `ai.apiKey` | （可选）DeepSeek API Key，留空则只推送新闻列表 |

### 第 2 步：测试发信

在项目目录下运行：

```powershell
python send_daily.py --test
```

如果提示找不到 python，用完整路径：

```powershell
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" send_daily.py --test
```

收到测试邮件即说明配置成功。

### 第 3 步：发送真实简报

```powershell
python send_daily.py             # 直接发送
python send_daily.py --dry-run   # 只生成本地 preview.html 预览，不发送
```

---

## 各邮箱 SMTP 授权码获取方法

程序会根据发件人域名自动选择 SMTP 服务器，你只需拿到**授权码**：

- **QQ 邮箱**：登录网页版 → 设置 → 账户 → 开启「SMTP 服务」→ 按提示生成授权码。
- **163 / 126**：设置 → POP3/SMTP/IMAP → 开启 SMTP → 生成授权码。
- **Gmail**：开启两步验证后，在「应用专用密码」里生成一个密码（当授权码用）。
- **Outlook**：若开启了双重验证，同样用「应用密码」；否则用登录密码。

> ⚠️ 授权码 ≠ 邮箱登录密码。用登录密码会发送失败。

## 申请 DeepSeek API Key（可选）

1. 打开 <https://platform.deepseek.com> 注册登录。
2. 左侧「API Keys」→「创建 API Key」，复制 `sk-...`。
3. 粘贴到 `config.json` 的 `ai.apiKey`。
4. 重新运行 `python send_daily.py`，简报里就会出现 AI 生成的摘要。

> API 费用极低：生成一封日报通常只需几分钱。

## 每天自动运行

以管理员身份打开 PowerShell，进入项目目录，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\schedule.ps1
```

默认每天 **08:00** 发送。改时间：

```powershell
powershell -ExecutionPolicy Bypass -File .\schedule.ps1 -AtTime "09:30"
```

> 该方式要求电脑在发送时间点是开机的。若想 24 小时在线稳定运行，可把项目放到一台云服务器上，用 `cron` 定时（Linux）或计划任务（Windows Server）。

## 自定义

- **换订阅源**：编辑 `config.json` 的 `news.feeds`，增删 URL（支持 RSS 2.0 和 Atom）。
- **调整条数**：`maxItemsPerFeed`（每源最多取几条）、`maxTotalItems`（总条数上限）。
- **换摘要风格**：编辑 `send_daily.py` 里 `build_prompt()` 的提示词。
- **改邮件外观**：编辑 `render_email_html()` 里的内联样式。

## 常见问题

**Q：邮件进了垃圾箱？**
新发信源可能被误判。把发件人加入通讯录/白名单，或标记「这不是垃圾邮件」即可。

**Q：提示授权码/密码错误？**
确认填的是授权码而非登录密码；确认已在邮箱设置里开启 SMTP。

**Q：某几个新闻源抓取失败？**
程序会自动跳过并继续。可在 `config.json` 里删除失效源、补充新源。

**Q：如何停用定时任务？**
```powershell
Unregister-ScheduledTask -TaskName "AI-Newsletter-Daily" -Confirm:$false
```
