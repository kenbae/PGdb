@echo off
chcp 65001 > nul
echo ============================================
echo   SSL 인증서 제거 (관리자 권한 필요)
echo ============================================
echo.

:: 관리자 권한 확인
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [오류] 관리자 권한으로 실행해주세요!
    echo.
    pause
    exit /b 1
)

echo TradeBot 인증서를 신뢰할 수 있는 루트에서 제거 중...
echo.

:: 인증서 제거 (CN=localhost로 검색)
certutil -delstore "Root" "localhost"

echo.
echo 완료! 브라우저를 재시작하세요.
echo.
pause
