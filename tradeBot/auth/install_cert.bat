@echo off
chcp 65001 > nul
echo ============================================
echo   SSL 인증서 신뢰 설정 (관리자 권한 필요)
echo ============================================
echo.

:: 관리자 권한 확인
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [오류] 관리자 권한으로 실행해주세요!
    echo.
    echo 방법: 이 파일을 우클릭 → "관리자 권한으로 실행"
    echo.
    pause
    exit /b 1
)

:: 인증서 경로
set CERT_PATH=%~dp0ssl\cert.pem

:: 인증서 존재 확인
if not exist "%CERT_PATH%" (
    echo [오류] 인증서 파일이 없습니다: %CERT_PATH%
    echo.
    echo 먼저 웹 서버를 한 번 실행하여 인증서를 생성하세요:
    echo   cd PGdb\tradeBot\servers
    echo   python web_server.py
    echo.
    pause
    exit /b 1
)

echo 인증서 파일: %CERT_PATH%
echo.
echo Windows 신뢰할 수 있는 루트 인증 기관에 등록 중...
echo.

:: 인증서 설치 (신뢰할 수 있는 루트 인증 기관)
certutil -addstore -f "Root" "%CERT_PATH%"

if %errorLevel% equ 0 (
    echo.
    echo ============================================
    echo   설치 완료!
    echo ============================================
    echo.
    echo 브라우저를 완전히 종료 후 다시 열어주세요.
    echo 이제 https://localhost:8888 접속 시 경고가 나타나지 않습니다.
    echo.
) else (
    echo.
    echo [오류] 인증서 설치 실패
    echo.
)

pause
