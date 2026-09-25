#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install Windows Scheduled Task: start Surge Watch at boot/logon.

.USAGE
  Right-click PowerShell -> Run as administrator
  cd C:\Users\kenne\PGdb
  powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1
#>
$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BatPath = Join-Path $ProjectRoot 'scripts\start_surge_watch.bat'
$TaskName = 'PGdbSurgeWatch'

if (-not (Test-Path $BatPath)) {
  throw "Missing start script: $BatPath"
}

Write-Host "Project : $ProjectRoot"
Write-Host "Script  : $BatPath"
Write-Host "Task    : $TaskName"

# Remove old task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument "/c `"$BatPath`"" `
  -WorkingDirectory $ProjectRoot

# At startup + at user logon (covers service boot and desktop login)
$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 5 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 0) `
  -MultipleInstances IgnoreNew

# Run as current user, highest privileges (needed for some firewall/network cases)
$principal = New-ScheduledTaskPrincipal `
  -UserId $env:USERNAME `
  -LogonType Interactive `
  -RunLevel Highest

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger @($triggerStartup, $triggerLogon) `
  -Settings $settings `
  -Principal $principal `
  -Description 'Auto-start PGdb BTCUSDT.P Surge Watch (http://0.0.0.0:8003)' `
  -Force | Out-Null

Write-Host ''
Write-Host '[OK] Scheduled task installed.' -ForegroundColor Green
Write-Host '     Name : PGdbSurgeWatch'
Write-Host '     When : At startup + at logon'
Write-Host '     URL  : http://<PC_IP>:8003'
Write-Host ''
Write-Host 'Start now?'
$ans = Read-Host 'Type Y to start immediately (Y/N)'
if ($ans -match '^[Yy]') {
  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 2
  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Format-List LastRunTime, LastTaskResult, NextRunTime
  Write-Host 'Check: netstat -an | findstr 8003   (expect 0.0.0.0:8003)'
}

Write-Host ''
Write-Host 'Uninstall later:'
Write-Host '  powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_surge_watch_autostart.ps1'
Pause
