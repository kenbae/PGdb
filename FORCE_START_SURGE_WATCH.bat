@echo off
REM One-click: kill :8003 and start Surge Watch on 0.0.0.0:8003
REM Double-click this file from Explorer, or run in CMD.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
if not exist "logs" mkdir logs
set LOG=logs\surge_watch_force.log

echo.
echo === FORCE START Surge Watch (0.0.0.0:8003) ===
echo Log: %CD%\%LOG%
echo.

echo ========== %date% %time% FORCE START ==========>> "%LOG%"

REM Kill every LISTENING PID on 8003
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
  echo Killing PID %%p
  echo Killing PID %%p>> "%LOG%"
  taskkill /F /PID %%p >nul 2>&1
)
timeout /t 2 /nobreak >nul

REM Find python
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
  echo [ERROR] python.exe not found
  echo [ERROR] python.exe not found>> "%LOG%"
  pause
  exit /b 1
)

echo Python: %PYEXE%
echo Python: %PYEXE%>> "%LOG%"
echo Starting: --host 0.0.0.0 --port 8003 --collect
echo Starting: --host 0.0.0.0 --port 8003 --collect>> "%LOG%"

REM Detached start so this window can show netstat
start "SurgeWatch" /MIN "%PYEXE%" -u "%CD%\surge_watch_api.py" --host 0.0.0.0 --port 8003 --collect

echo Waiting for bind...
set OK=0
for /L %%i in (1,1,15) do (
  timeout /t 1 /nobreak >nul
  netstat -an | findstr /C:"0.0.0.0:8003" | findstr "LISTENING" >nul 2>&1
  if not errorlevel 1 (
    set OK=1
    goto :done
  )
)

:done
echo.
echo Listen check:
netstat -an | findstr ":8003"
echo.
if "%OK%"=="1" (
  echo [OK] Bound to 0.0.0.0:8003
  echo [OK] Bound to 0.0.0.0:8003>> "%LOG%"
  echo Open http://127.0.0.1:8003  and  http://192.168.50.184:8003
) else (
  echo [FAIL] Not listening on 0.0.0.0:8003 — see %LOG%
  echo [FAIL] Not listening>> "%LOG%"
)
echo.
pause
