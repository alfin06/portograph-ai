<#
Restrict PortoGraph's private files to your Windows account (plus SYSTEM and
Administrators). By default files on D:\ inherit "Users: read" and "Authenticated
Users: modify", so any other account on this PC could read your bot token, holdings
and capital, or change them.

    powershell -ExecutionPolicy Bypass -File scripts\windows\lock-down-files.ps1           # apply
    powershell -ExecutionPolicy Bypass -File scripts\windows\lock-down-files.ps1 -Check    # show only

Covers: .env, database\ (DB, WAL files, backups), logs\. No admin rights needed for
files you own. Undo: icacls <path> /reset /T
#>
param([switch]$Check)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$targets = @(".env", "database", "logs") | ForEach-Object { Join-Path $root $_ } | Where-Object { Test-Path $_ }

function Show-Access($path) {
    (Get-Acl $path).Access | ForEach-Object { "    {0,-38} {1}" -f $_.IdentityReference, $_.FileSystemRights }
}

foreach ($t in $targets) {
    Write-Host "== $t"
    if (-not $Check) {
        $isDir = (Get-Item $t).PSIsContainer
        $inherit = if ($isDir) { "(OI)(CI)" } else { "" }
        # Stop inheriting from D:\, then allow only you, SYSTEM and Administrators
        & icacls $t /inheritance:r /grant:r "${me}:${inherit}F" "*S-1-5-18:${inherit}F" "*S-1-5-32-544:${inherit}F" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "icacls failed on $t" }
        if ($isDir) {
            # Existing files inside: reset them to inherit the folder's new rules
            & icacls (Join-Path $t "*") /reset /T /C /Q | Out-Null
        }
    }
    Show-Access $t
}
if (-not $Check) { Write-Host "`nDone. Only $me, SYSTEM and Administrators can open these now." }
