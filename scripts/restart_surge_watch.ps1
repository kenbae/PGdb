# Force-restart Surge Watch bound to 0.0.0.0:8003
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\restart_surge_watch.ps1
$ErrorActionPreference = 'Continue'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

Write-Host '=== Restart Surge Watch (0.0.0.0:8003) ===' -ForegroundColor Cyan
Write-Host "Root: $ProjectRoot"

# Kill anything listening on 8003
$killed = @()
$conns = Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
  $procId = $c.OwningProcess
  Write-Host "Killing PID $procId on port 8003"
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  $killed += $procId
}
netstat -ano | Select-String ':8003\s+.*LISTENING' | ForEach-Object {
  $parts = ($_ -split '\s+') | Where-Object { $_ }
  $procId = $parts[-1]
  if ($procId -match '^\d+$' -and $killed -notcontains [int]$procId) {
    Write-Host "Killing PID $procId (netstat)"
    taskkill /F /PID $procId 2>$null | Out-Null
  }
}
Start-Sleep -Seconds 2

# Confirm port is free
$still = netstat -an | Select-String ':8003\s+.*LISTENING'
if ($still) {
  Write-Host '[WARN] port 8003 still LISTENING after kill:' -ForegroundColor Yellow
  Write-Host $still
}

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
if (-not (Test-Path (Join-Path $ProjectRoot 'surge_watch_api.py'))) {
  throw "surge_watch_api.py not found in $ProjectRoot"
}

Write-Host "Python: $py"
if (-not (Test-Path 'logs')) { New-Item -ItemType Directory -Path 'logs' | Out-Null }
$log = Join-Path $ProjectRoot 'logs\surge_watch_autostart.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -Path $log -Value "========== restart $stamp =========="
Add-Content -Path $log -Value "PYEXE=$py"
Add-Content -Path $log -Value "CWD=$ProjectRoot"

# Prefer the bat launcher (handles log append + 127.0.0.1 cleanup).
# Run it detached so this PowerShell script can exit while the server keeps running.
$bat = Join-Path $ProjectRoot 'scripts\start_surge_watch.bat'
Write-Host "Launch: $bat"
$proc = Start-Process -FilePath $bat -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
Write-Host "Launcher PID: $($proc.Id)"

# Wait up to ~15s for LISTENING
$ok = $false
for ($i = 1; $i -le 15; $i++) {
  Start-Sleep -Seconds 1
  $listen = netstat -an | Select-String ':8003\s+.*LISTENING'
  if ($listen) {
    Write-Host ''
    Write-Host "Listen check (after ${i}s):"
    $listen | ForEach-Object { Write-Host "  $_" }
    if ($listen -match '0\.0\.0\.0:8003') {
      Write-Host '[OK] Bound to 0.0.0.0:8003' -ForegroundColor Green
      $ok = $true
      break
    }
    if ($listen -match '127\.0\.0\.1:8003') {
      Write-Host '[BAD] Still localhost-only' -ForegroundColor Red
    }
  } else {
    Write-Host "  waiting... ${i}s (no LISTENING yet)"
  }
}

if (-not $ok) {
  Write-Host ''
  Write-Host '[FAIL] Server did not listen on 0.0.0.0:8003' -ForegroundColor Red
  Write-Host '--- last 40 log lines ---'
  if (Test-Path $log) {
    Get-Content $log -Tail 40
  } else {
    Write-Host "(no log at $log)"
  }
  Write-Host ''
  Write-Host 'Manual fallback:'
  Write-Host "  cd $ProjectRoot"
  Write-Host "  `"$py`" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect"
  exit 1
}

Write-Host ''
Write-Host 'Open: http://127.0.0.1:8003  and  http://<LAN_IP>:8003'
Write-Host "Log:  $log"
