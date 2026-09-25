@echo off
REM Start BTCUSDT.P Surge Watch (bind all interfaces for LAN/DDNS)
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist "logs" mkdir logs

REM Prefer py launcher, fall back to python
set PY=python
where py >nul 2>&1 && set PY=py -3

echo [%date% %time%] Starting surge_watch_api on 0.0.0.0:8003 >> "logs\surge_watch_autostart.log"
%PY% -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect >> "logs\surge_watch_autostart.log" 2>&1
