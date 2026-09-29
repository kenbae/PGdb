# Update local PGdb checkout to the surge-watch branch (keeps your edits in stash)
# Usage (PowerShell):
#   cd C:\Users\kenne\PGdb
#   powershell -ExecutionPolicy Bypass -File .\scripts\update_local_surge_watch.ps1

$ErrorActionPreference = 'Stop'
$Branch = 'cursor/btcusdt-surge-watch-d45e'

Write-Host "=== Update local repo to $Branch ===" -ForegroundColor Cyan
Write-Host ("CWD: {0}" -f (Get-Location))

# Ensure we are in a git repo
git rev-parse --is-inside-work-tree | Out-Null

Write-Host '[1] Fetch origin...'
git fetch origin

Write-Host '[2] Stash local changes (including untracked)...'
$stashMsg = "auto-stash before update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
git stash push -u -m $stashMsg
Write-Host "    Stash message: $stashMsg"

Write-Host "[3] Checkout / reset to origin/$Branch ..."
git checkout -B $Branch "origin/$Branch"

Write-Host '[4] Show key files:'
@(
  'surge_watch_api.py',
  'surge_watch_dashboard.html',
  'scripts\start_surge_watch.bat',
  'scripts\install_surge_watch_autostart.ps1',
  'setup_firewall.bat',
  'setup_firewall.ps1'
) | ForEach-Object {
  if (Test-Path $_) { Write-Host "  [OK] $_" -ForegroundColor Green }
  else { Write-Host "  [MISSING] $_" -ForegroundColor Red }
}

Write-Host ''
Write-Host '[DONE] Local files updated.' -ForegroundColor Green
Write-Host 'Next (optional):'
Write-Host '  1) Firewall :  setup_firewall.bat  (Run as Administrator)'
Write-Host '  2) Autostart:  powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1'
Write-Host '  3) Run now  :  python surge_watch_api.py --host 0.0.0.0 --port 8003 --collect'
Write-Host ''
Write-Host 'Restore previous local edits later:'
Write-Host '  git stash list'
Write-Host '  git stash pop'
Write-Host ''
Pause
