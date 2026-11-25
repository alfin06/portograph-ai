# Start PortoGraph (bot + worker) automatically when you log on to Windows.
# Runs hidden (no console window) and is restarted by Task Scheduler if it dies.
# No admin rights needed: the task runs as you, only while you are logged on.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-autostart.ps1 -StartNow
param([switch]$StartNow)

$ErrorActionPreference = "Stop"
$TaskName = "PortoGraph AI"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $Pythonw)) { throw "No virtual environment at $Root\.venv - create it first (see README)." }
if (-not (Test-Path (Join-Path $Root ".env"))) { throw "No .env in $Root - add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first." }

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m app.run" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "PortoGraph AI: Telegram bot and market digests ($Root)" -Force | Out-Null
Write-Host "Installed: '$TaskName' starts at logon. Logs: $Root\logs"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started. Check with: .venv\Scripts\python.exe -m app.run --status"
}
