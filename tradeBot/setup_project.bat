@echo off
echo ========================================
echo Trading Bot 프로젝트 초기화
echo ========================================
echo.

REM 디렉토리 생성
echo [1/4] 디렉토리 생성 중...
mkdir config\strategies 2>nul
mkdir config\exchanges 2>nul
mkdir core 2>nul
mkdir strategies 2>nul
mkdir exchanges 2>nul
mkdir indicators\ict 2>nul
mkdir ai 2>nul
mkdir database 2>nul
mkdir monitoring 2>nul
mkdir logs 2>nul

REM __init__.py 파일 생성
echo [2/4] Python 패키지 초기화...
type nul > core\__init__.py
type nul > strategies\__init__.py
type nul > exchanges\__init__.py
type nul > indicators\__init__.py
type nul > indicators\ict\__init__.py
type nul > ai\__init__.py
type nul > database\__init__.py
type nul > monitoring\__init__.py

REM 설정 파일 생성
echo [3/4] 설정 파일 생성...
type nul > config\config.yaml
type nul > config\strategies\ict_ai.yaml
type nul > config\exchanges\binance_testnet.yaml
type nul > config\exchanges\binance_live.yaml

REM 기타 파일
echo [4/4] 기타 파일 생성...
type nul > .env
type nul > .env.example
type nul > requirements.txt
type nul > main.py
type nul > README.md

echo.
echo ========================================
echo 완료! 프로젝트 구조가 생성되었습니다.
echo ========================================
echo.
echo 다음 단계:
echo 1. .env 파일에 API 키 입력
echo 2. requirements.txt 작성
echo 3. pip install -r requirements.txt
echo.
pause
