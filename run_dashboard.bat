@echo off
REM ============================================================
REM Streamlit Dashboard 실행 스크립트 (Windows)
REM ============================================================

cd /d "%~dp0"

if not exist "logs\" mkdir logs

echo Starting Streamlit Dashboard...
echo Access at: http://localhost:8000
echo Logs: logs\streamlit.log
echo.

REM Streamlit 실행 (백그라운드)
start /min cmd /c "python -m streamlit run dashboard.py --server.port 8000 --server.address 0.0.0.0 --server.headless true > logs\streamlit.log 2>&1"

timeout /t 3 /nobreak >nul

REM 프로세스 확인
tasklist | find /i "python.exe" >nul
if %errorlevel% equ 0 (
    echo [OK] Dashboard started successfully!
    echo.
    echo Press any key to open in browser...
    pause >nul
    start http://localhost:8000
) else (
    echo [ERROR] Failed to start dashboard.
    pause
)
