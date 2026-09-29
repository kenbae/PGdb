# Diagnose Surge Watch autostart (no admin required for most checks)
$ErrorActionPreference = 'Continue'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TaskName = 'PGdbSurgeWatch'
$Log = Join-Path $ProjectRoot 'logs\surge_watch_autostart.log'

Write-Host '=== Surge Watch autostart check ===' -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host ''

Write-Host '[1] Scheduled task'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
  Write-Host '  [FAIL] Task PGdbSurgeWatch not found. Install with:' -ForegroundColor Red
  Write-Host '    powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1'
} else {
  $info = $task | Get-ScheduledTaskInfo
  Write-Host ("  State        : {0}" -f $task.State)
  Write-Host ("  LastRunTime  : {0}" -f $info.LastRunTime)
  Write-Host ("  LastResult   : {0} (0 = success)" -f $info.LastTaskResult)
  Write-Host ("  NextRunTime  : {0}" -f $info.NextRunTime)
  foreach ($t in $task.Triggers) {
    Write-Host ("  Trigger      : {0}" -f $t.CimClass.CimClassName)
  }
}

Write-Host ''
Write-Host '[2] Startup shortcut'
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'PGdbSurgeWatch.lnk'
if (Test-Path $lnk) { Write-Host "  [OK] $lnk" -ForegroundColor Green }
else { Write-Host "  [MISS] $lnk" -ForegroundColor Yellow }

Write-Host ''
Write-Host '[3] Port 8003'
$listen = netstat -an | Select-String ':8003\s+.*LISTENING'
if ($listen) {
  Write-Host $listen
  if ($listen -match '0\.0\.0\.0:8003') {
    Write-Host '  [OK] Listening on 0.0.0.0 (LAN OK)' -ForegroundColor Green
  } elseif ($listen -match '127\.0\.0\.1:8003') {
    Write-Host '  [BAD] Only 127.0.0.1 — restart with --host 0.0.0.0' -ForegroundColor Red
  }
} else {
  Write-Host '  [FAIL] Not listening' -ForegroundColor Red
}

Write-Host ''
Write-Host '[4] Log tail (logs\surge_watch_autostart.log)'
if (Test-Path $Log) {
  Get-Content $Log -Tail 30
} else {
  Write-Host '  [MISS] Log not created yet (task may never have run)' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Manual start:'
Write-Host '  Start-ScheduledTask -TaskName PGdbSurgeWatch'
Write-Host '  OR:  .\scripts\start_surge_watch.bat'
Write-Host 'Reinstall:'
Write-Host '  powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1 -StartNow'
Pause
