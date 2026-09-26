# Force-restart Surge Watch bound to 0.0.0.0:8003
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\restart_surge_watch.ps1
$ErrorActionPreference = 'Continue'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

Write-Host '=== Restart Surge Watch (0.0.0.0:8003) ===' -ForegroundColor Cyan

# Kill anything listening on 8003
$conns = Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
  $procId = $c.OwningProcess
  Write-Host "Killing PID $procId on port 8003"
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
# Fallback via netstat
netstat -ano | Select-String ':8003\s+.*LISTENING' | ForEach-Object {
  $parts = ($_ -split '\s+') | Where-Object { $_ }
  $procId = $parts[-1]
  if ($procId -match '^\d+$') {
    Write-Host "Killing PID $procId (netstat)"
    taskkill /F /PID $procId 2>$null
  }
}
Start-Sleep -Seconds 2

$py = $null
$candidates = @(
  "$env:LocalAppData\Programs\Python\Python312\python.exe",
  "$env:LocalAppData\Programs\Python\Python311\python.exe",
  "$env:LocalAppData\Programs\Python\Python310\python.exe",
  'C:\Python312\python.exe',
  'C:\Python311\python.exe'
)
foreach ($c in $candidates) { if (Test-Path $c) { $py = $c; break } }
if (-not $py) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}
if (-not $py) { throw 'python.exe not found' }

Write-Host "Python: $py"
if (-not (Test-Path 'logs')) { New-Item -ItemType Directory -Path 'logs' | Out-Null }
$log = Join-Path $ProjectRoot 'logs\surge_watch_autostart.log'

# Start-Process cannot redirect stdout+stderr to the SAME file.
# Use cmd.exe so both streams append to one log (same as start_surge_watch.bat).
$argList = @(
  '/c',
  "`"$py`" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect >> `"$log`" 2>&1"
)
Write-Host "Start: $py -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect"
Start-Process -FilePath 'cmd.exe' -ArgumentList $argList -WorkingDirectory $ProjectRoot -WindowStyle Hidden

Start-Sleep -Seconds 3
Write-Host ''
Write-Host 'Listen check:'
netstat -an | findstr ':8003'
Write-Host ''
Write-Host 'Expect: TCP  0.0.0.0:8003  ... LISTENING'
Write-Host 'Open:   http://127.0.0.1:8003  and  http://<LAN_IP>:8003'
Write-Host "Log:    $log"
