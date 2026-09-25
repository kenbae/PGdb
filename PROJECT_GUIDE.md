# PGdb 프로젝트 완전 가이드

> 암호화폐 자동 거래 시스템 - ICT + AI 기반

---

## 1. 프로젝트 개요

**PGdb**는 바이낸스 선물 자동 거래를 위한 통합 시스템입니다.

**핵심 기능:**
- 데이터 수집 → 지표 계산 → 신호 생성 → 결과 평가 자동화
- ICT(Inner Circle Trader) 전략 + AI(OLLAMA LLM) 검증
- 실시간 거래봇 & 백테스트 엔진
- 웹 대시보드 모니터링

---

## 2. 디렉토리 구조

```
PGdb/
├── config.yaml                    # 전역 설정 (DB, 거래소, 전략)
├── .env                           # API 키, 토큰
│
├── [데이터 수집/처리]
│   ├── run_ingest.py              # 바이낸스 촛대 데이터 수집
│   ├── run_indicators.py          # ATR, RSI, EMA 지표 계산
│   ├── run_signals.py             # 매매 신호 생성
│   ├── run_outcomes.py            # 신호 결과 평가
│   ├── run_pipeline.py            # 위 4개 통합 실행
│   ├── backfill_binance_vision.py # 과거 데이터 수집
│   ├── ema_scanner.py             # 실시간 EMA 크로스 스캐닝
│   ├── surge_watch.py             # BTCUSDT.P 급등 SETUP/TRIGGER 감시 (CLI)
│   ├── surge_watch_api.py         # 급등 감시 웹+수집기 (포트 8003)
│   └── surge_watch_dashboard.html # 급등 감시 대시보드 UI
│
├── [보고서/알림]
│   ├── run_report.py              # 전략 성과 리포트
│   ├── run_llm_reports.py         # AI 상세 리포트
│   ├── run_notify.py              # 텔레그램 실시간 알림
│   └── docs/SURGE_WATCH_GUIDE.md  # 2026-08-19 급등 복기 + 감시 가이드
│
├── [웹 서버]
│   ├── dashboard_api.py           # 대시보드 (포트 8001)
│   ├── process_manager.py         # 프로세스 관리 (포트 8000)
│   └── backtest_api.py            # 백테스트 API (포트 5000)
│
├── [백테스트]
│   ├── backtest_api.py            # 백테스트 웹 서버
│   ├── backtest_web.html          # 백테스트 UI
│   └── keltner_ict_turtle.py      # Keltner + ICT 전략
│
└── tradeBot/                      # 실시간 자동 거래봇
    ├── api_server.py              # 거래봇 API 서버
    ├── order_server.py            # 주문 실행 서버
    │
    ├── strategies/                # 거래 전략
    │   ├── ict_ai_strategy.py     # ICT + AI 통합 전략
    │   └── strategy_base.py       # 전략 베이스 클래스
    │
    ├── indicators/                # 기술적 지표
    │   ├── order_blocks.py        # Order Block 탐지
    │   ├── fair_value_gaps.py     # Fair Value Gap 탐지
    │   └── ict_strategy.py        # ICT 기본 지표
    │
    ├── exchanges/                 # 거래소 연동
    │   ├── binance_live.py        # 실전 바이낸스
    │   └── binance_testnet.py     # 테스트넷
    │
    ├── database/                  # DB 저장소
    │   ├── signals_repo.py        # 신호 저장소
    │   └── positions_repo.py      # 포지션 저장소
    │
    ├── ai/                        # AI 분석
    │   └── ollama_analyzer.py     # OLLAMA 분석기
    │
    └── static/                    # 웹 UI
        ├── index.html
        ├── signals.html
        └── positions.html
```

---

## 3. 설정 파일

### config.yaml (주요 설정)

```yaml
# 데이터베이스
db:
  host: localhost
  port: 5432
  name: marketdb
  user: trader
  password: "kh0070"

# 거래소
exchange:
  name: binance
  market: usdtm          # USDT-M 선물
  top_n: 50              # 상위 50개 심볼

# 타임프레임
timeframes:
  - 30m
  - 4h
  - 1d

# 데이터 수집
ingest:
  backfill_days: 90

# 텔레그램
telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"

# OLLAMA (AI)
ollama:
  host: "http://localhost:11434"
  llm_model: "qwen2.5:7b-instruct"
  embed_model: "bge-m3"

# 결과 평가
outcomes:
  horizon_bars:
    30m: 48    # 24시간
    4h: 30     # 5일
    1d: 20     # 20일

# 신호 생성
signals:
  ltf: "30m"
  strategy: "EMA20_50_CROSS_ALIGNED_HTF"
  score_threshold: 70
  atr_multiplier: 1.2

# 거래봇
trading:
  mode: "live"
  initial_capital: 100.0
  max_positions: 2
  max_leverage: 3
  risk_per_trade: 0.01
  max_daily_loss: 0.02
```

### .env

```env
# 텔레그램
TG_BOT_TOKEN=YOUR_BOT_TOKEN
TG_CHAT_ID=YOUR_CHAT_ID

# 바이낸스 API (실전)
BINANCE_LIVE_API_KEY=YOUR_API_KEY
BINANCE_LIVE_API_SECRET=YOUR_API_SECRET

# 거래봇 설정
INITIAL_CAPITAL=100
MAX_POSITIONS=2
MAX_LEVERAGE=3
RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.02
```

---

## 4. 실행 방법

### 4.1 기본 파이프라인

```bash
# 전체 파이프라인 한 번에 실행
python run_pipeline.py

# 또는 단계별 실행
python run_ingest.py       # 1. 데이터 수집
python run_indicators.py   # 2. 지표 계산
python run_signals.py      # 3. 신호 생성
python run_outcomes.py     # 4. 결과 평가
```

### 4.2 과거 데이터 수집 (Backfill)

```bash
# 단일 심볼
python backfill_binance_vision.py --symbol BTCUSDT --tf 30m --year 2025

# 병렬 다운로드 (빠름)
python backfill_binance_vision_parallel.py \
  --start-year 2024 --start-month 6 \
  --symbols BTCUSDT ETHUSDT SOLUSDT \
  --tfs 30m 4h 1d \
  --workers 8
```

### 4.3 EMA 스캐너

```bash
# 기본 실행
python ema_scanner.py

# 옵션 포함
python ema_scanner.py --timeframe 15m --top 50 --scan-every-sec 60

# AI 분석 포함
python ema_scanner.py --llm-alert
```

### 4.3.1 급등 사전 감시 (BTCUSDT.P)

```bash
# 웹 UI + 수집기 + 텔레그램 (포트 8003)
python surge_watch_api.py --port 8003 --collect

# 또는 Process Manager → "급등 감시" 시작
# http://localhost:8003

# CLI 1회 스냅샷
python surge_watch.py --once

# 상세: docs/SURGE_WATCH_GUIDE.md

# Windows 재부팅 자동 시작 (관리자 PowerShell, 1회)
# powershell -ExecutionPolicy Bypass -File .\scripts\install_surge_watch_autostart.ps1
```

### 4.4 보고서

```bash
python run_report.py       # 전략 성과 리포트 (텔레그램)
python run_llm_reports.py  # AI 상세 리포트
python run_notify.py       # 실시간 신호 알림
```

### 4.5 백테스트

```bash
# 백테스트 서버 시작
python backtest_api.py
# 브라우저: http://localhost:5000

# Keltner + ICT 백테스트
python keltner_ict_turtle.py --symbol BTCUSDT --tf 4h
```

### 4.6 대시보드 & 프로세스 관리

```bash
# 대시보드 (포트 8001)
python dashboard_api_modular.py --port 8001

# 프로세스 매니저 (포트 8000)
python process_manager.py
# 브라우저: http://localhost:8000

# 로그 숨기기
python process_manager.py --no-access-log
```

### 4.7 자동 거래봇

```bash
cd tradeBot

# API 서버 시작
python api_server.py
# 브라우저: http://localhost:8888

# 테스트넷 모드 (안전)
python api_server.py --mode testnet

# 실전 모드 (주의!)
python api_server.py --mode live
```

---

## 5. 웹 서버 포트 정리

| 서버 | 포트 | 설명 |
|------|------|------|
| process_manager.py | 8000 | 프로세스 관리 UI |
| dashboard_api_modular.py | 8001 | 트레이딩 대시보드 |
| tradeBot/api_server.py | 8888 | 거래봇 API |
| backtest_api.py | 5000 | 백테스트 API |

---

## 6. API 엔드포인트

### 프로세스 매니저 (8000)

```
GET  /                         # 관리자 UI
GET  /api/system               # 시스템 상태 (CPU, 메모리, GPU)
GET  /api/processes            # 프로세스 목록
POST /api/processes/{id}/start # 프로세스 시작
POST /api/processes/{id}/stop  # 프로세스 중지
WS   /ws/logs                  # 실시간 로그 (WebSocket)
```

### 대시보드 (8001)

```
GET  /                         # 메인 대시보드
GET  /api/signals              # 신호 목록
GET  /api/outcomes             # 결과 목록
GET  /api/performance          # 성과 통계
GET  /api/symbols              # 심볼 목록
```

### 거래봇 API (8888)

```
GET  /                         # 메인 UI
GET  /api/signals              # 신호 목록
GET  /api/positions            # 포지션 목록
GET  /api/watched-symbols      # 감시 심볼
POST /api/watched-symbols      # 감시 심볼 추가
GET  /api/futures/balance      # 선물 잔고
POST /api/futures/order        # 선물 주문
WS   /ws/logs                  # 실시간 로그
```

### 백테스트 (5000)

```
GET  /                         # 백테스트 UI
GET  /api/symbols              # 심볼 목록
POST /api/backtest             # 백테스트 실행
GET  /api/backtest/results     # 결과 조회
```

---

## 7. 데이터베이스 테이블

### 메인 테이블 (marketdb)

| 테이블 | 설명 |
|--------|------|
| candles | 촛대 데이터 (symbol, tf, ohlcv) |
| signals | 매매 신호 |
| outcomes | 신호 결과 평가 |

### 거래봇 테이블

| 테이블 | 설명 |
|--------|------|
| tradebot_signals | 거래봇 신호 |
| tradebot_signal_results | 신호 결과 |
| tradebot_positions | 포지션 |
| watched_symbols | 감시 심볼 |

---

## 8. 워크플로우

### 데이터 파이프라인
```
Binance API → run_ingest.py → DB (candles)
                    ↓
            run_indicators.py → DB (indicators)
                    ↓
            run_signals.py → DB (signals)
                    ↓
            run_outcomes.py → DB (outcomes)
                    ↓
            [Dashboard / Reports / Telegram]
```

### 거래봇 흐름
```
WebSocket Data → ICT 분석 → AI 검증 (OLLAMA)
                    ↓
              신호 생성 → DB 저장
                    ↓
              주문 실행 (Binance)
                    ↓
              포지션 추적 → Dashboard
```

---

## 9. 핵심 전략

### EMA Cross + HTF 정렬

```
1. EMA20/50 크로스오버 감지
2. 상위 타임프레임 정렬 확인
   - Golden Cross: EMA20 > EMA50 > EMA100
   - Dead Cross: EMA20 < EMA50 < EMA100
3. ATR 기반 SL/TP 계산
4. Score 70 이상만 신호 생성
```

### ICT + AI 전략

```
1. Order Block 탐지
2. Fair Value Gap 탐지
3. Liquidity Level 분석
4. OLLAMA AI 검증
   - 신뢰도 0.65 이상만 승인
   - 리스크 평가
   - 진입가/손절/익절 조정
5. 주문 실행
```

---

## 10. 안전 장치

### 리스크 관리

| 설정 | 값 | 설명 |
|------|-----|------|
| max_positions | 2 | 최대 동시 포지션 |
| max_leverage | 3x | 최대 레버리지 |
| risk_per_trade | 1% | 거래당 리스크 |
| max_daily_loss | 2% | 일일 최대 손실 |

### 신호 검증 체계

```
신호 생성 → HTF 정렬 확인 → ATR 검증 → Score 임계값
    ↓
AI 신뢰도 검증 (0.65 이상)
    ↓
실제 거래 또는 거절
```

---

## 11. 외부 접속 설정

### 포트포워딩

1. 공유기 관리자 페이지 접속 (192.168.0.1)
2. 포트포워딩 설정:
   - 외부 포트: 8000
   - 내부 IP: 서버 PC IP (예: 192.168.0.100)
   - 내부 포트: 8000
   - 프로토콜: TCP

### Windows 방화벽

```bash
# 관리자 권한으로 실행
netsh advfirewall firewall add rule ^
  name="Process Manager" ^
  dir=in action=allow protocol=TCP localport=8000
```

### 접속 방법

```
# 외부 네트워크
http://[공인IP]:8000

# 내부 네트워크
http://[로컬IP]:8000
```

---

## 12. 트러블슈팅

### 주문 오류: minimum amount precision

```
오류: amount of BTC/USDT must be greater than minimum amount precision of 0.001
원인: 주문 수량이 최소값(0.001 BTC)보다 작음
해결: 자동으로 최소값으로 조정됨 (api_server.py 수정됨)
```

### 주문 오류: notional must be no smaller than 100

```
오류: Order's notional must be no smaller than 100
원인: 주문 금액이 100 USDT 미만
해결: 자동으로 100 USDT 이상으로 조정됨 (api_server.py 수정됨)
```

### DB 연결 오류

```
원인: config.yaml DB 설정 오류
해결: host, port, user, password 확인
```

### OLLAMA 연결 오류

```
원인: ollama 서버 미실행
해결: ollama serve 실행 후 http://localhost:11434 확인
```

### WebSocket 로그 안보임

```
원인: 외부 네트워크에서 WebSocket 연결 문제
해결: process_manager.py 수정됨 (log_queue에 프로세스 로그 추가)
      서버 재시작 필요
```

### 데이터 불일치 (선물 vs 현물)

```
원인: run_ingest.py는 선물(usdtm) 데이터 수집,
      tradeBot은 현물(spot) 데이터로 업데이트
해결: api_server.py 수정됨 - 선물 데이터(usdtm)로 통일
      서버 재시작 필요
```

---

## 13. 데이터 수집 상세

### run_ingest.py

| 항목 | 내용 |
|------|------|
| 거래소 | Binance USDT-M 선물 (Perpetual) |
| 마켓 타입 | `usdtm` (USDT 마진 선물) |
| 데이터 종류 | OHLCV (Open, High, Low, Close, Volume) |
| 타임프레임 | 30m, 4h, 1d (config.yaml 설정) |
| 심볼 | 상위 50개 (24시간 거래량 기준) |
| 기간 | 최근 90일 (backfill_days) |

### candles 테이블 구조

```sql
- exchange: 'binance'
- symbol: 'BTCUSDT'
- market: 'usdtm'      # 선물 마켓
- tf: '30m', '4h', '1d'
- open_time: UTC 타임스탬프
- open, high, low, close, volume
```

> **중요**: run_ingest.py와 tradeBot/api_server.py 모두
> 선물(usdtm) 데이터를 사용하도록 통일됨

---

## 14. 주요 파일 요약

| 파일 | 역할 |
|------|------|
| run_pipeline.py | 데이터 파이프라인 통합 실행 |
| run_ingest.py | 바이낸스 선물 OHLCV 데이터 수집 |
| ema_scanner.py | 실시간 EMA 크로스 스캐닝 |
| surge_watch.py | BTCUSDT.P 급등 SETUP/TRIGGER 로컬 감시 (CLI) |
| surge_watch_api.py | 급등 감시 웹 대시보드 + 수집기 (포트 8003) |
| process_manager.py | 프로세스 관리 웹 UI |
| dashboard_api_modular.py | 트레이딩 대시보드 |
| backtest_api.py | 백테스트 서버 |
| tradeBot/api_server.py | 거래봇 API (포트 8888) |
| tradeBot/strategies/ict_ai_strategy.py | ICT + AI 전략 |
| ollama_analyzer.py | AI 신호 검증 |

---

## 15. 기술 스택

```
Backend:  Python, FastAPI, Flask, SQLAlchemy
Database: PostgreSQL
Exchange: Binance USDT-M Futures (CCXT)
AI/ML:    OLLAMA, qwen2.5:7b, bge-m3, FAISS
Frontend: HTML, Tailwind CSS, Chart.js
Realtime: WebSocket
Alert:    Telegram Bot API
```

---

## 16. 빠른 시작

```bash
# 1. DB 설정 확인
# config.yaml의 db 섹션 확인

# 2. 프로세스 매니저 시작
python process_manager.py

# 3. 브라우저에서 접속
# http://localhost:8000

# 4. 대시보드 시작 (프로세스 매니저에서)
# "대시보드" 프로세스 시작 클릭

# 5. 파이프라인 실행 (프로세스 매니저에서)
# "파이프라인" 프로세스 시작 클릭
```

---

*마지막 업데이트: 2026-01-21*
