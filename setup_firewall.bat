@echo off
REM 프로세스 매니저(8000) + 급등감시(8003) 등 웹 포트 방화벽 허용
echo ================================================
echo PGdb 웹 포트 방화벽 규칙 설정
echo ================================================

REM 관리자 권한 확인
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [ERROR] 관리자 권한이 필요합니다!
    echo 이 파일을 마우스 오른쪽 클릭 후 "관리자 권한으로 실행"을 선택하세요.
    echo.
    pause
    exit /b 1
)

echo.
echo [1/2] 기존 규칙 삭제 중...
netsh advfirewall firewall delete rule name="Process Manager Port 8000" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Surge Watch Port 8003" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Dashboard Port 8001" >nul 2>&1
netsh advfirewall firewall delete rule name="PGdb Data Dashboard Port 8002" >nul 2>&1

echo [2/2] 새 규칙 추가 중...

netsh advfirewall firewall add rule ^
    name="Process Manager Port 8000" ^
    dir=in action=allow protocol=TCP localport=8000 profile=any ^
    description="프로세스 매니저 웹 UI"

netsh advfirewall firewall add rule ^
    name="PGdb Dashboard Port 8001" ^
    dir=in action=allow protocol=TCP localport=8001 profile=any ^
    description="트레이딩 대시보드"

netsh advfirewall firewall add rule ^
    name="PGdb Data Dashboard Port 8002" ^
    dir=in action=allow protocol=TCP localport=8002 profile=any ^
    description="데이터 현황 대시보드"

netsh advfirewall firewall add rule ^
    name="PGdb Surge Watch Port 8003" ^
    dir=in action=allow protocol=TCP localport=8003 profile=any ^
    description="BTCUSDT.P 급등급락 감시 웹"

if %errorLevel% equ 0 (
    echo.
    echo ================================================
    echo [SUCCESS] 방화벽 규칙이 설정되었습니다!
    echo ================================================
    echo.
    echo 허용 포트: 8000, 8001, 8002, 8003  (TCP inbound)
    echo.
    echo 외부 접속 예:
    echo   Process Manager : http://[이PC_IP]:8000
    echo   급등/급락 감시  : http://[이PC_IP]:8003
    echo.
    echo IP 확인: ipconfig
    echo.
) else (
    echo.
    echo [ERROR] 방화벽 규칙 설정 실패
    echo.
)

echo 규칙 확인:
netsh advfirewall firewall show rule name="PGdb Surge Watch Port 8003"
echo.
pause
