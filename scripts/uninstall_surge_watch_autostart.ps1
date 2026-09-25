#Requires -RunAsAdministrator
$TaskName = 'PGdbSurgeWatch'
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task: $TaskName"
Pause
