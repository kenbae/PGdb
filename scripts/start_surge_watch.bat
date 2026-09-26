@echo off
REM Start Surge Watch on 0.0.0.0:8003 (LAN/DDNS). ASCII-only logs.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."
if not exist "logs" mkdir logs

set LOG=logs\surge_watch_autostart.log
echo ========== %date% %time% ==========>> "%LOG%"
echo CWD=%CD%>> "%LOG%"
echo PATH=%PATH%>> "%LOG%"

set PYEXE=
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set PYEXE=%LocalAppData%\Programs\Python\Python311\python.exe
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set PYEXE=%LocalAppData%\Programs\Python\Python310\python.exe
if exist "C:\Python312\python.exe" set PYEXE=C:\Python312\python.exe
if exist "C:\Python311\python.exe" set PYEXE=C:\Python311\python.exe

if not defined PYEXE (
  where python >nul 2>&1 && for /f "delims=" %%i in ('where python') do (
    set PYEXE=%%i
    goto :found
  )
)
:found

if not defined PYEXE (
  echo [ERROR] python.exe not found>> "%LOG%"
  exit /b 1
)

echo Using PYEXE=!PYEXE!>> "%LOG%"

REM If already listening on 8003, do not start a second instance
netstat -an | findstr /R /C:":8003 .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [SKIP] port 8003 already LISTENING>> "%LOG%"
  exit /b 0
)

echo Starting surge_watch_api --host 0.0.0.0 --port 8003 --collect>> "%LOG%"
"!PYEXE!" -u surge_watch_api.py --host 0.0.0.0 --port 8003 --collect >> "%LOG%" 2>&1
set RC=%errorlevel%
echo ExitCode=%RC% at %date% %time%>> "%LOG%"
exit /b %RC%
