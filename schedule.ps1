# 每天定时发送 AI 日报（Windows 任务计划程序）
# 用法：在项目目录下，打开 PowerShell 并执行（建议以管理员身份）：
#   powershell -ExecutionPolicy Bypass -File .\schedule.ps1
# 可修改下方 $AtTime 调整发送时间（24 小时制）

param(
  [string]$AtTime = "08:00"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 优先使用 DeepSeek Harness 自带的 Python；否则回退到系统 python
$BundledPython = "C:\Users\wxm\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (Test-Path $BundledPython) {
  $Python = $BundledPython
} else {
  $Python = "python"
}

$Script = Join-Path $ProjectDir "send_daily.py"
$TaskName = "AI-Newsletter-Daily"

$Action   = New-ScheduledTaskAction -Execute $Python -Argument ('"{0}"' -f $Script) -WorkingDirectory $ProjectDir
$Trigger  = New-ScheduledTaskTrigger -Daily -At $AtTime
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "每天抓取 AI 新闻并发送简报" -Force | Out-Null

Write-Host "已注册计划任务 '$TaskName'，每天 $AtTime 运行。"
Write-Host "如需立即手动测试，请在项目目录运行： python send_daily.py --test"
Write-Host "删除该任务： Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:$false"
