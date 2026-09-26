@echo off
REM Diagnose + force-start Surge Watch on 0.0.0.0:8003
REM Double-click OR:  FIX_NOW.bat
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
if not exist "logs" mkdir logs
set LOG=logs\surge_watch_force.log
set ERR=logs\surge_watch_startup_error.txt

echo.
echo === FIX NOW: Surge Watch ===
echo CWD=%CD%
echo.

echo ========== %date% %time% FIX_NOW ==========>> "%LOG%"
echo CWD=%CD%>> "%LOG%"

REM --- kill listeners on 8003 ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
  echo Killing PID %%p
  echo Killing PID %%p>> "%LOG%"
  taskkill /F /PID %%p >nul 2>&1
)
timeout /t 2 /nobreak >nul

REM --- find python ---
set PYEXE=
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python310\python.exe"
if not defined PYEXE if exist "C:\Python312\python.exe" set "PYEXE=C:\Python312\python.exe"
if not defined PYEXE (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    set "PYEXE=%%i"
    goto :have_py
  )
)
:have_py

if not defined PYEXE (
  echo [ERROR] python.exe not found. Install Python 3.11+ and retry.
  echo [ERROR] python.exe not found>> "%LOG%"
  pause
  exit /b 1
)
echo Python: %PYEXE%
echo Python: %PYEXE%>> "%LOG%"
"%PYEXE%" -V
"%PYEXE%" -V >> "%LOG%" 2>&1

if not exist "surge_watch_api.py" (
  echo [ERROR] surge_watch_api.py missing in %CD%
  echo [ERROR] wrong folder — need C:\Users\kenne\PGdb>> "%LOG%"
  pause
  exit /b 1
)

REM --- ensure deps ---
echo.
echo Checking imports...
"%PYEXE%" -c "import fastapi,uvicorn,yaml,requests; print('imports OK')" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Installing fastapi uvicorn pyyaml requests ...
  "%PYEXE%" -m pip install -U fastapi uvicorn pyyaml requests >> "%LOG%" 2>&1
)

"%PYEXE%" -c "import fastapi,uvicorn,yaml,requests; print('imports OK')"
if errorlevel 1 (
  echo [ERROR] Python packages still missing. See %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

REM --- dry-run import of app module ---
echo Importing surge_watch_api ...
"%PYEXE%" -c "import surge_watch_api; print('module OK')" > "%ERR%" 2>&1
if errorlevel 1 (
  echo [ERROR] surge_watch_api import failed:
  type "%ERR%"
  type "%ERR%" >> "%LOG%"
  pause
  exit /b 1
)
type "%ERR%"
type "%ERR%" >> "%LOG%"

REM --- start with log capture via cmd (NOT broken start quoting) ---
echo.
echo Starting server on 0.0.0.0:8003 ...
echo Starting server on 0.0.0.0:8003 ...>> "%LOG%"

REM Create a tiny helper launcher that redirects stdout+stderr
> "%TEMP%\surge_watch_run.cmd" (
  echo @echo off
  echo cd /d "%CD%"
  echo "%PYEXE%" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect ^>^> "%CD%\%LOG%" 2^>^&1
)

start "SurgeWatch" /MIN cmd /c "%TEMP%\surge_watch_run.cmd"

echo Waiting for bind...
set OK=0
for /L %%i in (1,1,20) do (
  timeout /t 1 /nobreak >nul
  netstat -an | findstr /C:"0.0.0.0:8003" | findstr "LISTENING" >nul 2>&1
  if not errorlevel 1 (
    set OK=1
    goto :done
  )
)

:done
echo.
echo ===== Listen check =====
netstat -an | findstr ":8003"
echo.
if "%OK%"=="1" (
  echo [OK] Bound to 0.0.0.0:8003
  echo [OK] Bound to 0.0.0.0:8003>> "%LOG%"
  echo.
  echo Open:
  echo   http://127.0.0.1:8003
  echo   http://192.168.50.184:8003
) else (
  echo [FAIL] Still not listening. Last log lines:
  echo -----
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 40"
  echo -----
  echo Full log: %CD%\%LOG%
  echo.
  echo Manual foreground start (copy errors from this window^):
  echo   "%PYEXE%" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect
)
echo.
pause
