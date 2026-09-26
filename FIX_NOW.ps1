# Diagnose + start Surge Watch on 0.0.0.0:8003
# powershell -ExecutionPolicy Bypass -File .\FIX_NOW.ps1
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location $Root
New-Item -ItemType Directory -Force -Path 'logs' | Out-Null
$Log = Join-Path $Root 'logs\surge_watch_force.log'
$Report = Join-Path $Root 'logs\surge_watch_report.txt'

function Log([string]$m) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
  Add-Content -Path $Log -Value $line
  Write-Host $m
}

Log '=== FIX_NOW.ps1 ==='
Log "Root=$Root"

# Kill :8003
Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  Log "Kill PID $($_.OwningProcess)"
  Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}
netstat -ano | Select-String ':8003\s+.*LISTENING' | ForEach-Object {
  $procId = ($_.ToString() -split '\s+')[-1]
  if ($procId -match '^\d+$') { Log "Kill PID $procId (netstat)"; taskkill /F /PID $procId 2>$null | Out-Null }
}
Start-Sleep -Seconds 2

# Python
$py = $null
foreach ($c in @(
  "$env:LocalAppData\Programs\Python\Python312\python.exe",
  "$env:LocalAppData\Programs\Python\Python311\python.exe",
  "$env:LocalAppData\Programs\Python\Python310\python.exe",
  'C:\Python312\python.exe'
)) { if (Test-Path $c) { $py = $c; break } }
if (-not $py) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}
if (-not $py) { throw 'python.exe not found' }
Log "Python=$py"
& $py -V 2>&1 | ForEach-Object { Log $_ }

if (-not (Test-Path 'surge_watch_api.py')) { throw "surge_watch_api.py missing in $Root" }

# Deps
$imp = & $py -c "import fastapi,uvicorn,yaml,requests; print('OK')" 2>&1
Log "import: $imp"
if ($LASTEXITCODE -ne 0) {
  Log 'pip install fastapi uvicorn pyyaml requests'
  & $py -m pip install -U fastapi uvicorn pyyaml requests 2>&1 | Tee-Object -FilePath $Log -Append | Out-Host
}

$mod = & $py -c "import surge_watch_api; print('module OK')" 2>&1
Log "module: $mod"
if ($LASTEXITCODE -ne 0) {
  $mod | Set-Content $Report
  notepad $Report
  throw 'surge_watch_api import failed — see report'
}

# Start detached with log append via cmd
$runCmd = @"
@echo off
cd /d "$Root"
"$py" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect >> "$Log" 2>&1
"@
$helper = Join-Path $env:TEMP 'surge_watch_run.cmd'
Set-Content -Path $helper -Value $runCmd -Encoding ASCII
Log "Launch $helper"
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$helper`"" -WorkingDirectory $Root -WindowStyle Minimized

$ok = $false
for ($i = 1; $i -le 20; $i++) {
  Start-Sleep -Seconds 1
  $listen = netstat -an | Select-String '0\.0\.0\.0:8003\s+.*LISTENING'
  if ($listen) { $ok = $true; Log $listen.ToString().Trim(); break }
  Write-Host "  wait ${i}s..."
}

$lines = @()
$lines += "Root=$Root"
$lines += "Python=$py"
$lines += "OK=$ok"
$lines += (netstat -an | Select-String ':8003' | ForEach-Object { $_.ToString() })
$lines += '--- log tail ---'
if (Test-Path $Log) { $lines += Get-Content $Log -Tail 30 }
$lines | Set-Content $Report -Encoding UTF8

if ($ok) {
  Log '[OK] 0.0.0.0:8003 LISTENING'
  Log 'Open http://127.0.0.1:8003 and http://192.168.50.184:8003'
} else {
  Log '[FAIL] not listening — opening report'
  notepad $Report
  exit 1
}
