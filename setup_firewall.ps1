#Requires -RunAsAdministrator
# PGdb firewall: open TCP 8000-8003 for external access
$ErrorActionPreference = 'Stop'
$ports = @(
  @{ Port = 8000; Name = 'Process Manager Port 8000' },
  @{ Port = 8001; Name = 'PGdb Dashboard Port 8001' },
  @{ Port = 8002; Name = 'PGdb Data Dashboard Port 8002' },
  @{ Port = 8003; Name = 'PGdb Surge Watch Port 8003' }
)

Write-Host '=== PGdb firewall setup (8000-8003) ===' -ForegroundColor Cyan

foreach ($p in $ports) {
  Get-NetFirewallRule -DisplayName $p.Name -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

  New-NetFirewallRule `
    -DisplayName $p.Name `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $p.Port `
    -Profile Any |
    Out-Null

  Write-Host ("[OK] {0}" -f $p.Port) -ForegroundColor Green
}

Write-Host ''
Write-Host 'Verify:' -ForegroundColor Cyan
Get-NetFirewallRule -DisplayName 'PGdb Surge Watch Port 8003' |
  Get-NetFirewallPortFilter |
  Format-Table Protocol, LocalPort

Write-Host 'Surge Watch URL: http://<PC_IP>:8003'
Pause
