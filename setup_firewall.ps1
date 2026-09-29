#Requires -RunAsAdministrator
# ASCII-only output (avoids Korean mojibake in consoles)
$ErrorActionPreference = 'Continue'
$ports = @(
  @{ Port = 8000; Name = 'Process Manager Port 8000' },
  @{ Port = 8001; Name = 'PGdb Dashboard Port 8001' },
  @{ Port = 8002; Name = 'PGdb Data Dashboard Port 8002' },
  @{ Port = 8003; Name = 'PGdb Surge Watch Port 8003' }
)

Write-Host '================================================'
Write-Host ' PGdb firewall setup  (ports 8000-8003)'
Write-Host '================================================'

foreach ($p in $ports) {
  Get-NetFirewallRule -DisplayName $p.Name -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
  try {
    New-NetFirewallRule `
      -DisplayName $p.Name `
      -Direction Inbound `
      -Action Allow `
      -Protocol TCP `
      -LocalPort $p.Port `
      -Profile Any | Out-Null
    Write-Host ("[OK]   port {0}" -f $p.Port)
  } catch {
    Write-Host ("[FAIL] port {0}  {1}" -f $p.Port, $_.Exception.Message)
  }
}

Write-Host ''
Write-Host 'Verify port 8003:'
Get-NetFirewallRule -DisplayName 'PGdb Surge Watch Port 8003' -ErrorAction SilentlyContinue |
  Get-NetFirewallPortFilter |
  Format-Table Protocol, LocalPort -AutoSize

Write-Host 'Surge Watch URL: http://YOUR_PC_IP:8003'
Pause
