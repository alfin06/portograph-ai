# Stop PortoGraph and remove the logon task created by install-autostart.ps1.
#   powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall-autostart.ps1
$TaskName = "PortoGraph AI"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path

& (Join-Path $Root ".venv\Scripts\python.exe") -m app.run --stop
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'. PortoGraph will no longer start at logon."
} else {
    Write-Host "'$TaskName' was not installed."
}
