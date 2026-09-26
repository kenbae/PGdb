#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install Windows Scheduled Task to auto-start Surge Watch after logon.

.USAGE
  cd C:\Users\kenne\PGdb
  powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1 -StartNow
#>
param(
  [switch]$StartNow,
  [int]$DelaySeconds = 45
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BatPath = Join-Path $ProjectRoot 'scripts\start_surge_watch.bat'
$TaskName = 'PGdbSurgeWatch'

if (-not (Test-Path $BatPath)) { throw "Missing: $BatPath" }

Write-Host "Project : $ProjectRoot"
Write-Host "Script  : $BatPath"
Write-Host "Task    : $TaskName"
Write-Host "Delay   : ${DelaySeconds}s after logon"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument "/c `"$BatPath`"" `
  -WorkingDirectory $ProjectRoot

# AtLogOn is reliable on desktop PCs (AtStartup+Interactive often fails before login)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Delay so network/disk are ready
$trigger.Delay = "PT${DelaySeconds}S"

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 10 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew `
  -RunOnlyIfNetworkAvailable:$false

$principal = New-ScheduledTaskPrincipal `
  -UserId $env:USERDOMAIN\$env:USERNAME `
  -LogonType Interactive `
  -RunLevel Highest

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'PGdb Surge Watch auto-start (0.0.0.0:8003) after user logon' `
  -Force | Out-Null

# Also drop a Startup-folder shortcut as backup
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'PGdbSurgeWatch.lnk'
$wscript = New-Object -ComObject WScript.Shell
$sc = $wscript.CreateShortcut($shortcutPath)
$sc.TargetPath = $BatPath
$sc.WorkingDirectory = $ProjectRoot
$sc.WindowStyle = 7
$sc.Description = 'PGdb Surge Watch'
$sc.Save()
Write-Host "Startup shortcut: $shortcutPath"

Write-Host ''
Write-Host '[OK] Autostart installed.' -ForegroundColor Green
Write-Host '     Task     : PGdbSurgeWatch (At logon + delay)'
Write-Host '     Backup   : Startup folder shortcut'
Write-Host '     Log      : logs\surge_watch_autostart.log'
Write-Host '     URL      : http://<PC_IP>:8003'

$doStart = $StartNow
if (-not $PSBoundParameters.ContainsKey('StartNow')) {
  $ans = Read-Host 'Start now? (Y/N)'
  $doStart = $ans -match '^[Yy]'
}
if ($doStart) {
  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 3
  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Format-List LastRunTime, LastTaskResult, NextRunTime
  netstat -an | findstr ':8003'
}

Write-Host ''
Write-Host 'Diagnose: powershell -ExecutionPolicy Bypass -File .\scripts\check_surge_watch_autostart.ps1'
if (-not $StartNow) { Pause }
