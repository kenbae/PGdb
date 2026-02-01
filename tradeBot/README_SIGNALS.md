# 실시간 신호 DB 저장 및 결과 추적 시스템

ICT + AI 전략에서 생성된 신호를 DB에 저장하고 결과를 추적하는 시스템입니다.

## 📋 개요

- **목적**: 생성된 모든 신호를 DB에 저장하고 시뮬레이션 결과 추적
- **AI 학습**: 저장된 데이터를 활용해 AI 모델 개선
- **성과 분석**: 전략별, 심볼별 성과 분석 및 통계

---

## 🗄️ 테이블 구조

### 1. tradebot_signals (신호 테이블)

실시간으로 생성된 모든 신호를 저장합니다.

```sql
CREATE TABLE tradebot_signals (
    id SERIAL PRIMARY KEY,
    signal_id VARCHAR(50) UNIQUE NOT NULL,  -- BTCUSDT_15m_buy_20260120123456

    -- 기본 정보
    strategy_name VARCHAR(50),
    symbol VARCHAR(20),
    timeframe VARCHAR(10),
    signal_type VARCHAR(10),  -- 'buy' or 'sell'

    -- 가격 정보
    entry_price DECIMAL(20, 8),
    stop_loss DECIMAL(20, 8),
    take_profit_1 DECIMAL(20, 8),
    take_profit_2 DECIMAL(20, 8),

    -- 신뢰도
    confidence DECIMAL(5, 4),
    risk_reward DECIMAL(10, 4),

    -- AI 분석
    ai_decision VARCHAR(20),  -- 'approve', 'reject', 'caution'
    ai_confidence DECIMAL(5, 4),
    ai_reasoning TEXT,
    ai_risk_assessment TEXT,
    ai_market_context TEXT,

    -- 메타데이터
    reasons JSONB,
    metadata JSONB,

    -- 실행 여부
    executed BOOLEAN DEFAULT false,
    execution_time TIMESTAMP,
    execution_price DECIMAL(20, 8),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 2. tradebot_signal_results (결과 테이블)

신호의 실제/시뮬레이션 결과를 저장합니다.

```sql
CREATE TABLE tradebot_signal_results (
    id SERIAL PRIMARY KEY,
    signal_id VARCHAR(50) UNIQUE NOT NULL,

    -- 결과
    status VARCHAR(20),  -- 'pending', 'tp1_hit', 'tp2_hit', 'sl_hit', 'expired', 'manual_close'
    exit_price DECIMAL(20, 8),
    exit_time TIMESTAMP,

    -- 성과
    pnl DECIMAL(20, 8),
    pnl_percent DECIMAL(10, 4),
    r_multiple DECIMAL(10, 4),

    -- 지표
    max_favorable_excursion DECIMAL(10, 4),  -- MFE %
    max_adverse_excursion DECIMAL(10, 4),     -- MAE %
    duration_minutes INTEGER,

    -- 시뮬레이션 여부
    is_simulation BOOLEAN DEFAULT true,

    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    FOREIGN KEY (signal_id) REFERENCES tradebot_signals(signal_id)
);
```

---

## 🚀 설치 및 설정

### 1. 테이블 생성

```bash
cd PGdb/tradeBot
python setup_signals_tables.py
```

**출력:**
```
🚀 signals 및 signal_results 테이블 생성 시작...
✅ DB 연결: localhost:5432/marketdb
✅ signals 및 signal_results 테이블 생성 완료!

📋 tradebot_signals 테이블: 0개 레코드
📋 tradebot_signal_results 테이블: 0개 레코드
```

### 2. 서버 시작

**분석 서버 (정시 분석 자동 실행):**
```bash
python servers/analyzer_server.py
```

**웹 서버 (API + 대시보드):**
```bash
python servers/web_server.py
```

서버가 시작되면:
- 분석 서버가 정시(0/15/30/45분)에 자동으로 감시 심볼 분석
- **AI 승인된 신호만** DB에 자동 저장
- 초기 결과 레코드 생성 (pending 상태)
- 웹 서버에서 신호 조회 및 대시보드 확인 가능

---

## 📡 API 엔드포인트

### 1. 신호 리스트 조회

```http
GET /api/signals/list?limit=100&offset=0&symbol=BTCUSDT&ai_decision=approve
```

**쿼리 파라미터:**
- `limit`: 최대 개수 (기본 100)
- `offset`: 오프셋 (페이징)
- `symbol`: 심볼 필터
- `timeframe`: 타임프레임 필터
- `signal_type`: 'buy' or 'sell'
- `ai_decision`: 'approve', 'reject', 'caution'
- `start_date`: 시작일 (YYYY-MM-DD)
- `end_date`: 종료일 (YYYY-MM-DD)

**응답:**
```json
{
  "signals": [
    {
      "signal_id": "BTCUSDT_15m_buy_20260120123456",
      "strategy_name": "ICT+AI",
      "symbol": "BTCUSDT",
      "timeframe": "15m",
      "signal_type": "buy",
      "entry_price": 43500.5,
      "stop_loss": 43200.0,
      "take_profit_1": 43980.0,
      "confidence": 0.85,
      "risk_reward": 1.618,
      "ai_decision": "approve",
      "ai_confidence": 0.78,
      "ai_reasoning": "Strong bullish Order Block with FVG confluence",
      "result_status": "pending",
      "pnl": null,
      "created_at": "2026-01-20T12:34:56"
    }
  ],
  "count": 1,
  "limit": 100,
  "offset": 0
}
```

### 2. 신호 상세 조회

```http
GET /api/signals/BTCUSDT_15m_buy_20260120123456
```

**응답:**
```json
{
  "signal_id": "BTCUSDT_15m_buy_20260120123456",
  "strategy_name": "ICT+AI",
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "entry_price": 43500.5,
  "reasons": ["bullish_ob", "bullish_fvg", "confluence"],
  "metadata": {
    "order_block": {...},
    "fvg": {...},
    "ai_analysis": {...}
  },
  "result_status": "tp1_hit",
  "pnl_percent": 1.2,
  "r_multiple": 1.8
}
```

### 3. 신호 통계 조회

```http
GET /api/signals/stats?symbol=BTCUSDT&days=30
```

**응답:**
```json
{
  "total_signals": 45,
  "wins": 28,
  "losses": 12,
  "win_rate": 0.70,
  "avg_pnl_percent": 1.35,
  "avg_r_multiple": 1.52,
  "total_pnl": 1250.75
}
```

---

## 🔄 신호 저장 플로우

### 1. 백그라운드 분석 (5분마다)

```python
async def background_analyzer():
    """백그라운드 분석"""
    while True:
        # 1. 최신 캔들 업데이트
        updated = await update_latest_candles(symbols, timeframe)

        # 2. 분석 및 신호 생성
        all_signals = []
        saved_count = 0

        for symbol in symbols:
            # 데이터 로드 & 분석
            results = strategy_manager.analyze_all(symbol, timeframe, df)

            for signal in signals:
                all_signals.append(signal)

                # AI 승인된 신호만 DB 저장
                if ai_decision == 'approve' and not rejected:
                    save_signal_to_db(signal, symbol, timeframe)
                    saved_count += 1

        logger.info(f"📊 분석 완료: {len(all_signals)}개 신호, {saved_count}개 DB 저장")

        await asyncio.sleep(300)  # 5분
```

### 2. 신호 저장 함수

```python
def save_signal_to_db(signal, symbol, timeframe):
    """신호를 DB에 저장"""
    # 1. signal_id 생성
    signal_id = f"{symbol}_{timeframe}_{signal.signal_type}_{timestamp}"

    # 2. tradebot_signals 테이블에 저장
    signal_data = {
        'signal_id': signal_id,
        'strategy_name': signal.strategy_name,
        'symbol': symbol,
        'entry_price': signal.entry_price,
        'ai_decision': ai_analysis.get('decision'),
        ...
    }
    signals_repo.save_signal(signal_data)

    # 3. tradebot_signal_results 테이블에 초기 레코드 생성
    result_data = {
        'signal_id': signal_id,
        'status': 'pending',
        'is_simulation': True,
        ...
    }
    signals_repo.save_result(result_data)
```

---

## 📊 활용 사례

### 1. 전략 성과 분석

```bash
curl "http://localhost:8888/api/signals/stats?days=30"
```

승률, 평균 수익률, R-Multiple 등 통계 확인

### 2. 심볼별 필터링

```bash
curl "http://localhost:8888/api/signals/list?symbol=BTCUSDT&ai_decision=approve&limit=50"
```

특정 심볼의 승인된 신호만 조회

### 3. 결과 기반 AI 학습

```python
# DB에서 승인/거부된 신호 로드
approved_signals = signals_repo.get_signals(ai_decision='approve')
rejected_signals = signals_repo.get_signals(ai_decision='reject')

# 결과와 함께 학습 데이터 구성
for signal in approved_signals:
    if signal['result_status'] == 'tp1_hit':
        # 승인 & 성공 케이스
        training_data.append({
            'features': signal['metadata'],
            'label': 'good_signal'
        })
```

---

## 🎯 다음 단계

### 1. 결과 업데이트 백그라운드 작업 ✅

**구현 완료!** 5분마다 pending 상태 신호를 확인하고 TP/SL 달성 여부를 자동 업데이트합니다.

**주요 기능:**
- 최근 72시간 내 pending 신호 조회
- 신호 생성 이후 캔들 데이터 분석
- TP1/TP2/SL 달성 여부 확인
- PnL, R-Multiple, MFE, MAE 자동 계산
- DB에 결과 자동 저장

**위치:** [servers/web_server.py](servers/web_server.py) - `background_result_updater()` 함수

### 2. 대시보드 HTML ✅

**구현 완료!** 신호 리스트를 시각화하는 웹 대시보드

**접속:** http://localhost:8888/signals

**주요 기능:**
- 신호 리스트 테이블 (페이징 지원)
- 필터링 (심볼, 타입, AI 결정, 결과 상태)
- 실시간 통계 (전체 신호, 승률, 평균 PnL, R-Multiple)
- 신호 상세 모달
- 30초마다 자동 새로고침

**위치:** [static/signals.html](static/signals.html)

### 3. AI 피드백 루프

결과를 기반으로 AI 모델 개선 (향후 구현)

---

## ✅ 완료된 기능

- [x] DB 테이블 설계 및 생성
- [x] Signals Repository 구현
- [x] API 서버 통합
- [x] 백그라운드 분석에서 자동 저장
- [x] 신호 리스트 조회 API
- [x] 신호 상세 조회 API
- [x] 신호 통계 API
- [x] 결과 업데이트 백그라운드 작업
- [x] 신호 리스트 대시보드 HTML

---

## 📌 참고

- **기존 signals 테이블**: PGdb 프로젝트에서 사용 중이므로 `tradebot_signals`로 구분
- **자동 저장**: AI 승인된 신호만 자동 저장 (`ai_decision='approve'` AND `not rejected`)
- **시뮬레이션**: 모든 결과는 `is_simulation=true`로 저장 (실제 거래와 구분)

---

## 🔍 트러블슈팅

### 테이블이 없을 때

```bash
python setup_signals_tables.py
```

### 신호가 저장 안 될 때

1. 로그 확인:
   ```
   📊 분석 완료: 3개 신호, 1개 DB 저장
   ```

2. AI 결정 확인:
   - `approve` + `rejected=false`만 저장됨
   - 거부된 신호는 메모리에만 유지 (WebSocket 전송)

### 통계가 0으로 나올 때

- `tradebot_signal_results` 테이블에 결과가 있는지 확인
- 결과 업데이트 백그라운드 작업 필요 (다음 단계)
