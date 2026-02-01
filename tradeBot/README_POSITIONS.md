# 마이 포지션 - 바이낸스 거래 히스토리 시스템

바이낸스 계좌의 실제 거래 포지션을 DB에 저장하고 분석하는 시스템입니다.

## 📋 개요

- **목적**: 바이낸스 계좌의 실제 거래 기록 저장 및 분석
- **데이터**: 포지션 진입/종료, 손익, 수수료, 레버리지 등
- **분석**: 승률, 평균 손익, 일별 수익, 거래 패턴 분석

---

## 🗄️ 테이블 구조

### tradebot_positions (포지션 테이블)

바이낸스 계좌의 실제 거래 포지션을 저장합니다.

```sql
CREATE TABLE IF NOT EXISTS tradebot_positions (
    id SERIAL PRIMARY KEY,
    position_id VARCHAR(50) UNIQUE NOT NULL,  -- 고유 ID

    -- 기본 정보
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'buy' (LONG) or 'sell' (SHORT)

    -- 가격 정보
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,

    -- 손익 정보
    pnl DECIMAL(20, 8),  -- 실현 손익 (USDT)
    pnl_percent DECIMAL(10, 4),  -- 손익률 (%)
    commission DECIMAL(20, 8),  -- 수수료

    -- 포지션 상태
    status VARCHAR(20) NOT NULL,  -- 'open', 'closed', 'liquidated'

    -- 레버리지 및 마진
    leverage INTEGER,
    margin DECIMAL(20, 8),

    -- 시간 정보
    open_time TIMESTAMP NOT NULL,
    close_time TIMESTAMP,
    duration_minutes INTEGER,

    -- 메타데이터
    order_ids JSONB,  -- 관련 주문 ID들
    metadata JSONB,
    raw_data JSONB,  -- 바이낸스 원본 데이터

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 🚀 설치 및 설정

### 1. 테이블 생성

```bash
cd PGdb/tradeBot
python setup_positions_table.py
```

**출력:**
```
🚀 포지션 히스토리 테이블 생성 시작...
✅ DB 연결: localhost:5432/marketdb
✅ 포지션 히스토리 테이블 생성 완료!

📋 tradebot_positions 테이블: 0개 레코드
```

### 2. 바이낸스 API 설정

`.env` 파일에 바이낸스 API 키 추가:

```env
BINANCE_LIVE_API_KEY=your_api_key_here
BINANCE_LIVE_API_SECRET=your_api_secret_here
```

**주의사항:**
- API 권한: 거래 조회 권한 필요
- IP 제한 설정 권장
- 2FA 인증 필수

---

## 📡 API 엔드포인트

### 1. 포지션 리스트 조회

```http
GET /api/positions/list?limit=100&offset=0&symbol=BTCUSDT&side=buy&status=closed
```

**쿼리 파라미터:**
- `limit`: 최대 개수 (기본 100)
- `offset`: 오프셋 (페이징)
- `symbol`: 심볼 필터
- `side`: 'buy' (LONG) or 'sell' (SHORT)
- `status`: 'open', 'closed', 'liquidated'
- `start_date`: 시작일 (YYYY-MM-DD)
- `end_date`: 종료일 (YYYY-MM-DD)

**응답:**
```json
{
  "positions": [
    {
      "position_id": "order_12345",
      "symbol": "BTCUSDT",
      "side": "buy",
      "entry_price": 43500.50,
      "exit_price": 44200.00,
      "quantity": 0.1,
      "pnl": 70.00,
      "pnl_percent": 1.61,
      "commission": 2.50,
      "status": "closed",
      "leverage": 10,
      "margin": 435.00,
      "open_time": "2026-01-20T10:00:00",
      "close_time": "2026-01-20T15:30:00",
      "duration_minutes": 330
    }
  ],
  "count": 1,
  "limit": 100,
  "offset": 0
}
```

### 2. 포지션 상세 조회

```http
GET /api/positions/order_12345
```

**응답:**
```json
{
  "position_id": "order_12345",
  "symbol": "BTCUSDT",
  "side": "buy",
  "entry_price": 43500.50,
  "exit_price": 44200.00,
  "quantity": 0.1,
  "pnl": 70.00,
  "pnl_percent": 1.61,
  "commission": 2.50,
  "status": "closed",
  "leverage": 10,
  "margin": 435.00,
  "order_ids": ["123", "124"],
  "metadata": {...},
  "raw_data": {...}
}
```

### 3. 포지션 통계 조회

```http
GET /api/positions/stats?symbol=BTCUSDT&days=30
```

**응답:**
```json
{
  "total_positions": 50,
  "wins": 32,
  "losses": 18,
  "win_rate": 0.64,
  "avg_pnl": 15.50,
  "avg_pnl_percent": 1.25,
  "total_pnl": 775.00,
  "max_win": 150.00,
  "max_loss": -80.00,
  "avg_duration_minutes": 240,
  "total_commission": 45.00
}
```

### 4. 일별 손익 조회

```http
GET /api/positions/daily-pnl?symbol=BTCUSDT&days=30
```

**응답:**
```json
{
  "daily_pnl": [
    {
      "date": "2026-01-20",
      "trades": 3,
      "daily_pnl": 125.50,
      "avg_pnl_percent": 1.35,
      "commission": 5.50
    },
    {
      "date": "2026-01-19",
      "trades": 2,
      "daily_pnl": -45.00,
      "avg_pnl_percent": -0.85,
      "commission": 3.20
    }
  ],
  "count": 2
}
```

---

## 🎨 웹 대시보드

### 접속

```
http://localhost:8888/positions
```

### 주요 기능

**1. 실시간 통계 카드**
- 전체 포지션 수
- 수익/손실 거래 수
- 승률
- 총 수익
- 평균 수익
- 총 수수료
- 평균 지속시간

**2. 포지션 리스트**
- 테이블 형식으로 표시
- 페이징 지원 (50개/페이지)
- 필터링 (심볼, 방향, 상태)
- 클릭시 상세 모달

**3. 일별 손익 차트 (예정)**
- 일별 손익 추이 그래프
- Chart.js 또는 Plotly 사용 예정

**4. 자동 새로고침**
- 30초마다 자동 업데이트

---

## 🔄 바이낸스 데이터 가져오기

### BinanceLive 클래스 메서드

#### 1. 거래 히스토리 조회

```python
from exchanges.binance_live import BinanceLive

# 초기화
exchange = BinanceLive(
    api_key="your_key",
    api_secret="your_secret"
)

# 특정 심볼 거래 조회
trades = exchange.get_trade_history(
    symbol="BTC/USDT",
    since=1640995200000,  # 2022-01-01
    limit=500
)

# 전체 심볼 거래 조회 (상위 10개)
all_trades = exchange.get_trade_history(limit=100)
```

#### 2. 수입 히스토리 조회

```python
# 실현 손익 조회
income = exchange.get_income_history(
    symbol="BTCUSDT",
    income_type="REALIZED_PNL",
    since=1640995200000,
    limit=100
)

# 수수료 조회
commissions = exchange.get_income_history(
    income_type="COMMISSION",
    limit=100
)
```

### 데이터 저장 예제

```python
from database.positions_repo import PositionsRepo
from datetime import datetime

# Repository 초기화
positions_repo = PositionsRepo(db_engine)

# 포지션 데이터 생성
position_data = {
    'position_id': 'order_12345',
    'symbol': 'BTCUSDT',
    'side': 'buy',  # 'buy' or 'sell'
    'entry_price': 43500.50,
    'exit_price': 44200.00,
    'quantity': 0.1,
    'pnl': 70.00,
    'pnl_percent': 1.61,
    'commission': 2.50,
    'status': 'closed',
    'leverage': 10,
    'margin': 435.00,
    'open_time': datetime(2026, 1, 20, 10, 0, 0),
    'close_time': datetime(2026, 1, 20, 15, 30, 0),
    'duration_minutes': 330,
    'order_ids': [123, 124],
    'metadata': {'strategy': 'ICT+AI'},
    'raw_data': {}  # 바이낸스 원본 데이터
}

# 저장
positions_repo.save_position(position_data)
```

---

## 📊 활용 사례

### 1. 실제 거래 성과 분석

```bash
curl "http://localhost:8888/api/positions/stats?days=30"
```

실제 계좌의 승률, 평균 손익 등 확인

### 2. 심볼별 수익성 분석

```bash
curl "http://localhost:8888/api/positions/list?symbol=BTCUSDT&status=closed&limit=100"
```

특정 심볼의 종료된 포지션만 조회하여 수익성 분석

### 3. 시뮬레이션과 실전 비교

```python
# 신호 시스템의 시뮬레이션 결과
from database.signals_repo import SignalsRepo
signals_stats = signals_repo.get_statistics(days=30)

# 실제 거래 결과
from database.positions_repo import PositionsRepo
positions_stats = positions_repo.get_statistics(days=30)

# 비교
print(f"시뮬레이션 승률: {signals_stats['win_rate']*100:.1f}%")
print(f"실전 승률: {positions_stats['win_rate']*100:.1f}%")
```

### 4. 일별 손익 추적

```bash
curl "http://localhost:8888/api/positions/daily-pnl?days=30"
```

일별 손익을 조회하여 수익 패턴 분석

---

## 🔄 바이낸스 동기화

### 수동 동기화 (구현 완료 ✅)

**마이 포지션 페이지에서 버튼 클릭으로 동기화:**

1. http://localhost:8888/positions 접속
2. 헤더의 "🔄 바이낸스 동기화" 버튼 클릭
3. 최근 30일 거래 자동 가져오기

**서비스 레이어:**
```python
from services.binance_sync import BinanceSyncService

# 서비스 초기화
sync_service = BinanceSyncService(exchange, positions_repo)

# 동기화 실행
result = sync_service.sync_positions(days=30)
# {'success': True, 'message': '...', 'saved': 10, 'total': 50}
```

**주요 기능:**
- 거래 히스토리를 포지션으로 자동 변환 (FIFO 매칭)
- 손익, 손익률, 수수료 자동 계산
- 중복 방지 (position_id 기반)
- 진행 상태 및 결과 알림

**파일:**
- [services/binance_sync.py](services/binance_sync.py) - 동기화 서비스
- [servers/web_server.py](servers/web_server.py) - API 엔드포인트
- [static/positions.html](static/positions.html) - UI 버튼

---

## 🎯 다음 단계

### 1. 바이낸스 자동 동기화 (백그라운드)

주기적으로 바이낸스에서 거래 히스토리를 가져와 DB에 저장

```python
async def auto_sync_binance():
    """바이낸스 자동 동기화 (1시간마다)"""
    sync_service = BinanceSyncService(exchange, positions_repo)

    while True:
        try:
            result = sync_service.sync_positions(days=1)
            logger.info(f"✅ 자동 동기화: {result['saved']}개 저장")
        except Exception as e:
            logger.error(f"❌ 자동 동기화 실패: {e}")

        await asyncio.sleep(3600)  # 1시간마다
```

### 2. 차트 구현

Chart.js 또는 Plotly를 사용한 일별 손익 차트

### 3. 알림 기능

- 일일 손익 요약 알림
- 최대 손실 알림
- 주간 성과 리포트

### 4. 고급 분석

- 심볼별 수익률 분석
- 레버리지별 성과 비교
- 거래 시간대별 분석
- 평균 보유 시간 분석

---

## ✅ 완료된 기능

- [x] DB 테이블 설계 및 생성
- [x] Positions Repository 구현
- [x] 바이낸스 거래/수입 히스토리 API 연동
- [x] API 서버 통합
- [x] 포지션 리스트 조회 API
- [x] 포지션 상세 조회 API
- [x] 포지션 통계 API
- [x] 일별 손익 API
- [x] 마이 포지션 대시보드 HTML
- [x] 바이낸스 수동 동기화 (버튼 클릭)
- [x] 동기화 서비스 레이어 분리
- [ ] 바이낸스 자동 동기화 (백그라운드)
- [ ] 일별 손익 차트 구현

---

## 📌 참고

- **테이블 이름**: `tradebot_positions` (기존 positions 테이블과 구분)
- **바이낸스 API**: CCXT 라이브러리 사용
- **데이터 타입**: USDT-M 선물 거래
- **메인 페이지**: http://localhost:8888/positions

---

## 🔍 트러블슈팅

### 테이블이 없을 때

```bash
python setup_positions_table.py
```

### 바이낸스 API 연결 실패

1. `.env` 파일에 API 키 확인
2. API 권한 확인 (거래 조회 필요)
3. IP 제한 설정 확인

### 데이터가 없을 때

- 바이낸스에서 실제 거래가 있어야 데이터가 표시됩니다
- 테스트용 더미 데이터를 수동으로 추가할 수 있습니다

---

## 🔗 관련 문서

- [신호 시스템](README_SIGNALS.md) - ICT+AI 신호 추적
- [ROADMAP](ROADMAP.md) - 전체 프로젝트 로드맵
- [CHANGELOG](CHANGELOG.md) - 변경 이력
