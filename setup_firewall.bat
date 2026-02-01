@echo off
REM 프로세스 매니저 포트 8000 방화벽 설정
echo ================================================
echo 프로세스 매니저 방화벽 규칙 설정
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

echo [2/2] 새 규칙 추가 중...
netsh advfirewall firewall add rule ^
    name="Process Manager Port 8000" ^
    dir=in ^
    action=allow ^
    protocol=TCP ^
    localport=8000 ^
    profile=any ^
    description="프로세스 매니저 웹 인터페이스 포트"

if %errorLevel% equ 0 (
    echo.
    echo ================================================
    echo [SUCCESS] 방화벽 규칙이 설정되었습니다!
    echo ================================================
    echo.
    echo 포트: 8000
    echo 프로토콜: TCP
    echo 프로파일: 모든 네트워크
    echo.
) else (
    echo.
    echo [ERROR] 방화벽 규칙 설정 실패
    echo.
)

echo 규칙 확인:
netsh advfirewall firewall show rule name="Process Manager Port 8000"

echo.
pause
