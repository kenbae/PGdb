@echo off
chcp 65001 >nul
setlocal EnableExtensions
REM PGdb web ports: 8000 / 8001 / 8002 / 8003
echo ================================================
echo PGdb firewall setup (8000-8003)
echo ================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Run as Administrator.
    echo Right-click this file -^> Run as administrator
    echo.
    pause
    exit /b 1
)

echo.
echo Removing old rules...
netsh advfirewall firewall delete rule name="Process Manager Port 8000" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Dashboard Port 8001" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Data Dashboard Port 8002" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Surge Watch Port 8003" >nul 2>&1

echo.
echo Adding inbound TCP allow rules...
set FAIL=0

netsh advfirewall firewall add rule name="Process Manager Port 8000" dir=in action=allow protocol=TCP localport=8000 profile=any
if errorlevel 1 ( echo [FAIL] 8000 & set FAIL=1 ) else ( echo [OK]   8000 Process Manager )

netsh advfirewall firewall add rule name="PGdb Dashboard Port 8001" dir=in action=allow protocol=TCP localport=8001 profile=any
if errorlevel 1 ( echo [FAIL] 8001 & set FAIL=1 ) else ( echo [OK]   8001 Dashboard )

netsh advfirewall firewall add rule name="PGdb Data Dashboard Port 8002" dir=in action=allow protocol=TCP localport=8002 profile=any
if errorlevel 1 ( echo [FAIL] 8002 & set FAIL=1 ) else ( echo [OK]   8002 Data Dashboard )

netsh advfirewall firewall add rule name="PGdb Surge Watch Port 8003" dir=in action=allow protocol=TCP localport=8003 profile=any
if errorlevel 1 ( echo [FAIL] 8003 & set FAIL=1 ) else ( echo [OK]   8003 Surge Watch )

echo.
echo ================================================
if "%FAIL%"=="0" (
    echo [SUCCESS] Ports 8000 8001 8002 8003 allowed.
) else (
    echo [WARN] Some rules failed. See [FAIL] lines above.
)
echo ================================================
echo.
echo External URLs:
echo   Process Manager : http://[PC_IP]:8000
echo   Surge Watch     : http://[PC_IP]:8003
echo.
echo Verify 8003 rule:
netsh advfirewall firewall show rule name="PGdb Surge Watch Port 8003"
echo.
pause
endlocal
