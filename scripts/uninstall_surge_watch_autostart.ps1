#Requires -RunAsAdministrator
$TaskName = 'PGdbSurgeWatch'
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'PGdbSurgeWatch.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force }
Write-Host "[OK] Removed task and Startup shortcut: $TaskName"
Pause
