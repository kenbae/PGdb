# tradeBot 개발 히스토리

> **중요**: 새 세션 시작 시 이 파일을 먼저 읽어 프로젝트 구조와 이전 이슈를 파악하세요.

---

## 프로젝트 구조

```
PGdb/
├── strategies/                     # 🔥 공용 전략 폴더 (메인)
│   ├── __init__.py                 # 전략 export
│   ├── base.py                     # BaseStrategy, TradeSignal
│   ├── strategy_manager.py         # StrategyManager
│   ├── ema_cross_strategy.py       # EMA Cross 전략
│   ├── ict_strategy.py             # ICT 전략
│   ├── rsi_strategy.py             # RSI 전략
│   ├── bollinger_strategy.py       # Bollinger Band 전략
│   ├── keltner_ict_turtle_strategy.py  # Keltner ICT Turtle 전략
│   ├── pattern_rag_strategy.py     # Pattern RAG 전략
│   ├── bb_adaptive_rsi_strategy.py # BB 3σ + Adaptive RSI 전략
│   └── keltner_ict_turtle_backtester.py # 백테스트 전용
│
├── backtest_api.py                 # 백테스터 API 서버
├── strategy_backtester.py          # 백테스터 엔진
│
└── tradeBot/
    ├── static/                     # 웹 UI
    │   ├── index.html              # 대시보드 (메인 페이지)
    │   ├── signals.html            # 신호 페이지 (필터링, 병합 뷰)
    │   └── positions.html          # 포지션 페이지
    │
    ├── servers/                    # 서버
    │   ├── web_server.py           # FastAPI 웹 서버 (API + 정적 파일)
    │   └── analyzer_server.py      # 분석 서버
    │
    ├── database/                   # DB 관련
    │   ├── signals_repo.py         # 신호 DB 레포지토리
    │   ├── positions_repo.py       # 포지션 DB 레포지토리
    │   ├── watched_symbols_repo.py # 관심 심볼 DB 레포지토리
    │   ├── signals_schema.sql      # 신호 테이블 스키마
    │   ├── positions_schema.sql    # 포지션 테이블 스키마
    │   ├── watched_symbols_schema.sql
    │   └── migrations/             # DB 마이그레이션 SQL
    │
    ├── strategies/                 # 🔗 리다이렉트 (→ PGdb/strategies/)
    │   └── __init__.py             # 공용 폴더로 리다이렉트
    │
    ├── indicators/                 # 기술적 지표
    │   ├── technical.py            # 기술적 지표 계산
    │   ├── order_blocks.py         # 오더 블록 탐지
    │   └── fair_value_gaps.py      # FVG 탐지
    │
    ├── ai/                         # AI 분석
    │   ├── ollama_analyzer.py      # Ollama 기반 AI 분석
    │   └── pattern_rag.py          # 패턴 RAG
    │
    ├── services/                   # 서비스
    │   ├── binance_sync.py         # 바이낸스 동기화
    │   └── order_client.py         # 주문 클라이언트
    │
    ├── core/                       # 코어 모듈
    │   ├── config_loader.py        # 설정 로더
    │   ├── websocket_feed.py       # 웹소켓 피드
    │   └── websocket_saver.py      # 웹소켓 데이터 저장
    │
    ├── exchanges/                  # 거래소 연동
    │   ├── binance_live.py         # 바이낸스 라이브
    │   └── binance_testnet.py      # 바이낸스 테스트넷
    │
    ├── HISTORY.md                  # 이 파일 (개발 히스토리)
    ├── config.yaml                 # 설정 파일
    └── start_all.py                # 전체 서버 시작
```

---

## 전략 시스템

### 전략 폴더 구조
- **메인**: `PGdb/strategies/` - 백테스터와 오토봇이 공유하는 공용 전략 폴더
- **리다이렉트**: `tradeBot/strategies/__init__.py` - 공용 폴더로 자동 리다이렉트

### 사용 가능한 전략

| 전략 | 클래스명 | 설명 |
|------|---------|------|
| EMA Cross | EMACrossStrategy | EMA 크로스오버 + HTF 정렬 |
| ICT | ICTStrategy | Order Block + Fair Value Gap |
| RSI | RSIStrategy | RSI 과매수/과매도 |
| Bollinger | BollingerStrategy | 볼린저 밴드 Breakout/Reversal |
| Keltner ICT Turtle | KeltnerICTTurtleStrategy | 켈트너 채널 + ICT + 터틀 통합 |
| Pattern RAG | PatternRAGStrategy | 과거 패턴 학습 + AI 신호 생성 |
| BB Adaptive RSI | BBAdaptiveRSIStrategy | BB 3σ + Kaufman ER 기반 Adaptive RSI |

### 전략 사용 예시 (Python)
```python
from strategies import create_strategy_manager, TradeSignal

# 전략 매니저 생성
manager = create_strategy_manager(config)

# 특정 전략으로 분석
signals = manager.analyze('BTCUSDT', '15m', df, strategies=['ema_cross', 'ict'])
```

---

## 데이터베이스 접속 정보

설정 파일 위치: `PGdb/config.yaml`

```yaml
db:
  host: localhost
  port: 5432
  name: marketdb
  user: trader
  password: "kh0070"
```

SQLAlchemy 연결 문자열:
```
postgresql://trader:kh0070@localhost:5432/marketdb
```

Python에서 접속 예시:
```python
from sqlalchemy import create_engine, text

db_url = 'postgresql://trader:kh0070@localhost:5432/marketdb'
engine = create_engine(db_url)

with engine.connect() as conn:
    result = conn.execute(text('SELECT * FROM tradebot_signals LIMIT 5'))
    for row in result:
        print(row)
```

---

## 주요 테이블 스키마

### tradebot_signals
- `signal_id`: 고유 ID
- `strategy_name`: 전략 이름 (ema_cross, ict, rsi, bollinger)
- `symbol`: 심볼 (BTCUSDT)
- `signal_type`: buy/sell
- `entry_price`, `stop_loss`, `take_profit_1`, `take_profit_2`
- `confidence`, `risk_reward`
- `ai_decision`: approve/reject/caution
- `matched_position_id`: 매칭된 실제 포지션 ID

### tradebot_positions
- `position_id`: 고유 ID
- `symbol`, `side`
- `entry_price`, `exit_price`, `quantity`
- `pnl`, `pnl_percent`
- `source`: 데이터 출처 (binance, csv, manual)
- `open_time`, `close_time`

---

## 자주 발생하는 이슈 패턴

### 1. 타임스탬프 변환
- PostgreSQL에서 가져온 시간은 UTC로 저장되지만 `T`와 마이크로초만 포함
- JavaScript `Date` 파싱 시 타임존 표시자(`Z` 또는 `+09:00`) 필요
- **해결**: 타임존 표시자가 없는 경우에만 `Z` 추가
```javascript
let openTimeStr = pos.open_time || '';
if (openTimeStr && !openTimeStr.endsWith('Z') && !openTimeStr.includes('+')) {
    openTimeStr += 'Z';
}
const timestamp = new Date(openTimeStr).getTime();
```

### 2. NaN/Infinity JSON 직렬화
- Python에서 `float('nan')` 또는 `float('inf')`가 있으면 JSON 직렬화 실패
- **해결**: API 응답 전 `math.isnan()`, `math.isinf()` 체크 후 `None`으로 변환
- **관련 파일**: `servers/web_server.py`

### 3. 전역 변수 누락
- `signals`, `binanceTrades` 등 전역 변수 선언 필수
- `signals.html` 상단에 선언 확인

### 4. API 필터 파라미터
- 클라이언트 전용 필터(`_source` 등)는 API 호출 전 제거 필요
- `delete apiFilters._source` 처리

### 5. API 응답 필드명 불일치
- 프론트엔드에서 `strategy_name` 사용 시, API도 동일한 키 사용 필수
- `/api/signals/current`에서 `strategy` → `strategy_name`으로 통일
- **관련 파일**: `servers/web_server.py`

### 6. CSS Grid 컬럼 수 불일치
- 테이블 헤더 컬럼 추가 시 `.table-header`와 `.signal-row`의 `grid-template-columns` 동시 수정 필요
- **관련 파일**: `static/index.html` (스타일 섹션)

### 8. 다크 테마 체크박스 스타일 (표준)
- **모든 HTML 파일에서 동일한 체크박스 스타일 사용**
- **CSS 코드**:
```css
input[type="checkbox"] {
    accent-color: #f0b90b;
    background: #2b3139 !important;
    border: 1px solid #3b424b !important;
    appearance: none;
    -webkit-appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 3px;
    cursor: pointer;
    position: relative;
}
input[type="checkbox"]:checked {
    background: #f0b90b !important;
    border-color: #f0b90b !important;
}
input[type="checkbox"]:checked::after {
    content: '✓';
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: #0b0e11;
    font-size: 12px;
    font-weight: bold;
}
```
- **적용 파일**: `backtest_web.html`, `process_manager.html`

### 9. 다크 테마 시작 버튼 스타일 (표준)
- **모든 HTML 파일에서 동일한 시작/실행 버튼 스타일 사용**
- **색상**:
  - 배경: 짙은 노란색 (`#c99a09`)
  - 텍스트: 검정 (`#0b0e11`)
  - 호버: 더 어두운 노란색 (`#a88008`)
  - 비활성화 배경: 짙은 회색 (`#2b3139`)
  - 비활성화 텍스트: 회색 (`#5e6673`)
- **CSS 코드**:
```css
/* 시작/실행 버튼 - 활성화 */
.start-button {
    background: #c99a09 !important;
    color: #0b0e11 !important;
}
.start-button:hover {
    background: #a88008 !important;
}

/* 시작/실행 버튼 - 비활성화 */
.start-button:disabled {
    background: #2b3139 !important;
    color: #5e6673 !important;
    cursor: not-allowed;
}
```
- **적용 파일**: `backtest_web.html`, `process_manager.html`

### 7. 가격 정밀도 (SL/TP 라운딩) ⚠️ 반복 이슈
- **문제**: 저가 코인(1000PEPEUSDT, 1000BONKUSDT, SHIBUSDT 등)에서 SL/TP가 4-5자리로 라운딩됨
- **증상**: `$0.0052000`, `$0.00920000` 처럼 뒷자리가 0으로 잘림
- **원인**: 전략 파일에서 SL/TP 계산 시 `round(x, 4)` 또는 라운딩 누락
- **해결**: 모든 가격 계산에 `round(x, 8)` 적용
- **수정 필요 파일** (✅ 완료):
  - `PGdb/strategies/base.py`: `create_signal()`에서 모든 가격에 `round(x, 8)` 적용
  - `PGdb/strategies/pattern_rag_strategy.py`: SL/TP에 `round(x, 8)` 명시적 적용
  - `PGdb/strategies/ict_strategy.py`: 매수/매도 SL/TP 계산
  - `PGdb/strategies/bollinger_strategy.py`: reversal 모드 SL/TP 계산
  - `tradeBot/ai/pattern_rag.py`: AI 분석 SL/TP
- **검증**: DB 스키마(`signals_schema.sql`)는 `DECIMAL(20, 8)`로 8자리 지원

---

## 개발 히스토리

### 2026-01-23: 심볼별 전략 필터링 기능 ✅ 완료

#### 기능 설명
watched symbols에서 설정된 전략만 분석하도록 수정

#### 주요 변경
1. **AnalyzerState 확장**
   - `symbol_strategies` 딕셔너리 추가 (`{symbol: [strategy1, strategy2, ...]}`
   - 전략 미설정 시 `None` → 모든 전략 사용

2. **심볼별 전략 파싱**
   - DB `strategies` 컬럼에서 쉼표로 구분된 문자열을 리스트로 변환
   - 예: `"keltner_ict_turtle,bollinger"` → `['keltner_ict_turtle', 'bollinger']`

3. **run_analysis에서 전략 필터 적용**
   - `strategy_manager.analyze_all(symbol, timeframe, df, strategy_filter=strategy_filter)`

#### 수정 파일
- `servers/analyzer_server.py`: AnalyzerState, init_components, run_analysis, reload-symbols, analyze/now API

#### 사용법
- watched symbols UI에서 전략 설정 시 해당 전략만 분석
- 전략 미설정 시 모든 전략 분석

---

### 2026-01-23: Signal Detail 모달 차트 기능 ✅ 완료

#### 기능 설명
View Details 모달에서 신호의 타임프레임 기준 캔들차트와 Entry/SL/TP 라인 표시

#### 주요 기능
1. **Lightweight Charts 라이브러리 추가**
   - CDN: `https://unpkg.com/lightweight-charts@4.1.0`

2. **차트 컨테이너**
   - 신호 상세 모달 너비 확대 (450px → 700px)
   - 차트 높이 300px (모바일 200px)

3. **자동 데이터 로드**
   - DB에 캔들 데이터 없으면 바이낸스에서 자동 fetch
   - `/api/sync-candles/{symbol}` API 추가

4. **가격 라인 표시**
   - Entry: 파란색 실선
   - Stop Loss: 빨간색 점선
   - Take Profit 1: 녹색 점선
   - Take Profit 2: 진한 녹색 점선

#### 수정 파일
1. **`static/index.html`**
   - lightweight-charts 라이브러리 추가
   - 차트 컨테이너 및 범례 HTML
   - `loadSignalChart()` 함수
   - 창 크기 변경 시 리사이즈 처리

2. **`servers/web_server.py`**
   - `GET /api/sync-candles/{symbol}` API 추가

---

### 2026-01-23: Watched Symbols 정렬 및 드래그앤드롭 ✅ 완료

#### 기능 설명
- ABC 순 정렬 버튼
- 드래그앤드롭으로 심볼 순서 변경
- 순서 변경 시 DB 자동 저장

#### 주요 변경
1. **DB 스키마**
   - `sort_order` 컬럼 추가

2. **Repository 메서드**
   - `update_sort_order()`: 심볼 순서 업데이트
   - `sort_alphabetically()`: 알파벳순 정렬

3. **API 엔드포인트**
   - `POST /api/watched-symbols/update-order`
   - `POST /api/watched-symbols/sort-alphabetically`

#### 수정 파일
- `database/watched_symbols_repo.py`
- `servers/web_server.py`
- `static/index.html`: 드래그앤드롭 CSS/JS

---

### 2026-01-23: Bulk Settings 모달 ✅ 완료

#### 기능 설명
모든 심볼의 타임프레임/전략을 일괄 변경하는 설정 모달

#### 주요 기능
1. **심볼 선택**
   - 전체 선택/해제 체크박스
   - 개별 심볼 선택

2. **일괄 변경 옵션**
   - 타임프레임 변경
   - 전략 변경 (다중 선택)

#### 수정 파일
- `static/index.html`: 모달 HTML, `applyBulkSettings()` 함수
- `servers/web_server.py`: bulk update API

---

### 2026-01-23: AI 프롬프트 편집 기능 추가 ✅ 완료

#### 기능 설명
AI 시장 분석 모달에서 프롬프트를 직접 편집하고 저장할 수 있는 기능 추가

#### 주요 기능
1. **프롬프트 파일 분리**: `tradeBot/ai/prompts/market_analysis.txt`
2. **모달 내 편집기**: `📝 편집` 버튼으로 프롬프트 확인/수정
3. **저장 기능**: `💾 저장` 버튼으로 파일에 영구 저장
4. **실시간 적용**: 편집기 열린 상태에서 분석 시 수정된 프롬프트 사용

#### 수정 파일
1. **`servers/web_server.py`**
   - `GET /api/ai-prompt`: 프롬프트 파일 읽기 API
   - `POST /api/ai-prompt`: 프롬프트 파일 저장 API
   - `AIAnalysisRequest`에 `custom_prompt` 필드 추가

2. **`static/index.html`**
   - 프롬프트 편집기 UI (textarea)
   - `loadAIPrompt()`: 프롬프트 파일 로드
   - `togglePromptEditor()`: 편집기 토글
   - `savePrompt()`: 프롬프트 저장

3. **`ai/prompts/market_analysis.txt`** (신규)
   - AI 분석 프롬프트 템플릿
   - `{market_summary}` 플레이스홀더는 자동 대체

#### 사용법
1. AI 분석 모달에서 `📝 편집` 클릭
2. 프롬프트 내용 확인/수정
3. `💾 저장` 클릭하여 파일에 저장
4. 편집기 열린 상태로 분석 실행 시 수정된 프롬프트 적용

---

### 2026-01-23: AI 시장 분석 기능 추가 ✅ 완료

#### 기능 설명
트레이딩봇 대시보드에 AI 시장 분석 기능 추가
- Analyze 버튼 옆에 "🤖 AI 분석" 버튼 추가
- 모달에서 심볼, 타임프레임, Lookback 캔들 수 설정
- DB에서 캔들 로드 후 부족하면 바이낸스에서 자동 fetch
- OLLAMA AI로 종합 시장 분석 실행

#### AI 분석 내용
1. **전체 시장 평가**: 현재 시장 상황 종합 평가
2. **트렌드 분석**: 단기/중기/장기 트렌드
3. **주요 지지/저항선**: 기술적으로 중요한 가격대
4. **매매 전략 제안**: 롱/숏 추천, 진입/손절/목표가
5. **리스크 평가**: 현재 진입의 리스크 수준
6. **주의사항**: 트레이더가 알아야 할 중요한 점

#### 사용 방법
1. 심볼 클릭하여 선택
2. "🤖 AI 분석" 버튼 클릭
3. 타임프레임, Lookback 캔들 수 설정
4. "분석 실행" 버튼 클릭
5. AI 분석 결과 확인

#### 수정 파일
1. **`static/index.html`**
   - AI 분석 버튼 추가
   - AI 분석 모달 HTML 추가
   - JavaScript 함수: `openAIAnalysisModal()`, `runAIAnalysis()`, `formatAIReport()`

2. **`servers/web_server.py`**
   - `POST /api/ai-analysis` API 엔드포인트 추가
   - 캔들 데이터 로드 및 바이낸스 fetch 로직
   - 기술적 지표 계산 (EMA, RSI, ATR, BB)
   - OLLAMA AI 프롬프트 구성 및 호출

#### API 사용 예시
```bash
curl -X POST http://localhost:8888/api/ai-analysis \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTCUSDT", "timeframe": "15m", "lookback": 100}'
```

#### 응답 예시
```json
{
  "success": true,
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "candles_analyzed": 100,
  "market_data": {
    "current_price": 104500.0,
    "trend": "uptrend",
    "rsi": 55.2,
    "volatility": "normal"
  },
  "ai_report": {
    "market_assessment": "...",
    "trade_suggestion": {
      "direction": "long",
      "entry": 104400,
      "stop_loss": 103800,
      "take_profit_1": 105500
    }
  }
}
```

---

### 2026-01-23: 전략별 AI 검증 프롬프트 추가 ✅ 완료

#### 문제
- `bb_adaptive_rsi` 등 신규 전략에서 AI 검증 시 ICT 전용 프롬프트가 사용됨
- 전략 특성에 맞지 않는 AI 판단 발생

#### 해결
- `ollama_analyzer.py`에 `_get_strategy_guide()` 메서드 추가
- 전략별 맞춤형 AI 검증 가이드 제공:
  - **BB Adaptive RSI**: 3σ 극단 영역 + Adaptive RSI 역추세 전략
  - **ICT**: Order Block + FVG 구조
  - **Bollinger**: 밴드 돌파/복귀 전략
  - **RSI**: 과매수/과매도 역추세
  - **Keltner ICT Turtle**: 채널 돌파 + 터틀 시스템
  - **EMA Cross**: 골든/데드크로스 추세추종

#### 수정 파일
1. **`tradeBot/ai/ollama_analyzer.py`**
   - `_get_strategy_guide()` 메서드 추가
   - `analyze_signal()` 프롬프트에서 전략별 가이드 사용

2. **`PGdb/strategies/strategy_manager.py`**
   - `_apply_ai_filter()`에서 전략명을 AI 분석에 전달

---

### 2026-01-23: 심볼별 타임프레임 지원 ✅ 완료
- **문제**: 웹 UI에서 심볼별 timeframe을 30m으로 설정해도 분석 시 15m으로 실행됨
- **원인**:
  1. `analyzer_server.py`: 모든 심볼에 동일한 `state.timeframe = '15m'` 사용
  2. `index.html`: `runAnalysis()`에서 timeframe이 `15m`으로 하드코딩
- **해결**:
  1. `analyzer_server.py`:
     - `AnalyzerState`에 `symbol_timeframes` 딕셔너리 추가
     - `init_components()`에서 `get_all_with_strategies()`로 심볼별 timeframe 로드
     - `update_latest_candles()`: 심볼별 timeframe 사용
     - `run_analysis()`: 각 심볼의 timeframe으로 분석
     - `/api/analyze/now`: 분석 전 최신 심볼 정보 로드
     - `/api/reload-symbols`: 심볼 정보 수동 갱신 API 추가
  2. `index.html`:
     - 단일 심볼 분석: `symbolData.timeframe` 사용
     - 전체 심볼 분석: 각 `symData.timeframe` 사용
- **수정 파일**:
  - `servers/analyzer_server.py`
  - `static/index.html`

### 2026-01-23: 트레이딩봇 대시보드 개선 ✅ 완료
1. **스크롤 문제 수정**
   - `body`와 `.main-container`를 고정 높이에서 `min-height`로 변경
   - `.center-panel`, `.right-panel`에 `overflow-y: auto` 추가
   - 수정 파일: `static/index.html`

2. **분석 모달 오버레이 추가**
   - Analyze 버튼 클릭 시 전체 화면 모달 표시
   - 스피너 + 진행 상태 텍스트 표시
   - 분석 중 다른 UI 클릭 차단
   - 수정 파일: `static/index.html`

3. **심볼 설정 모달에 타임프레임 선택 추가**
   - 심볼 편집 모달에 TF 드롭다운 추가 (1m ~ 1d)
   - API에서 timeframe 파라미터 처리
   - 수정 파일:
     - `static/index.html`: 모달 UI, JS 함수
     - `servers/web_server.py`: `UpdateStrategiesRequest`에 timeframe 필드 추가
     - `database/watched_symbols_repo.py`: `update_strategies()`에 timeframe 파라미터 추가

### 2026-01-23: 백테스터 "실시간 분석으로 등록" 버그 수정 ✅ 완료
1. **timeframe is not defined 에러**
   - 원인: `timeframe` 변수 대신 `timeframes` 배열 사용해야 함
   - 수정: `timeframe: timeframes.join(',')`

2. **전략 값이 `,,0,1,2,3,4,5,6`으로 저장되는 문제**
   - 원인: `Object.keys(strategies)`가 배열 인덱스 반환
   - 수정: `selectedStrategies` state 직접 사용

3. **전략 값이 `_,a,b,d,e,i,p,r,s,t,v`으로 저장되는 문제**
   - 원인: 서버에서 문자열에 `','.join()` 호출 시 문자별 분리
   - 수정: `backtest_api.py`에서 `isinstance(strategies_raw, str)` 체크 추가
   - 수정 파일:
     - `PGdb/backtest_web.html`
     - `PGdb/backtest_api.py`

### 2026-01-22: 모바일 반응형 디자인 추가 ✅ 완료
- **목적**: 모든 페이지에서 모바일/태블릿 지원
- **수정 파일**:
  - `static/index.html`: 모바일 하단 네비게이션, 햄버거 메뉴, 신호 카드 뷰
  - `static/signals.html`: 필터 수직 정렬, 통계 카드 2열, 테이블 수평 스크롤
  - `static/positions.html`: 필터/통계 모바일 최적화, 차트 1열 레이아웃
- **브레이크포인트**:
  - `1024px 이하`: 태블릿 최적화 (그리드 축소)
  - `768px 이하`: 모바일 레이아웃 (단일 컬럼, 햄버거 메뉴)
  - `480px 이하`: 소형 모바일 (폰트/패딩 추가 축소)
- **주요 기능**:
  - index.html: 모바일 하단 네비게이션 (Symbols / Signals / Trade / Positions)
  - 신호 테이블 → 카드 형식 변환 (`data-label` 속성 활용)
  - 햄버거 메뉴 버튼 + 드롭다운 네비게이션
  - `initMobileNav()` / `toggleMobileMenu()` 함수 추가

### 2026-01-22: SL/TP 가격 정밀도 수정 (8자리) - 2차 수정 ✅ 완료
- **문제**: 1000BONKUSDT, 1000PEPEUSDT 등 저가 코인에서 SL/TP가 여전히 4-5자리로 잘림
- **원인 분석**:
  - pattern_rag 전략에서 생성되는 신호가 정밀도 손실
  - `PGdb/strategies/base.py`의 `create_signal()`에서 라운딩 미적용
  - `PGdb/strategies/pattern_rag_strategy.py`에서 SL/TP 직접 전달 시 라운딩 미적용
- **수정 파일**:
  - `PGdb/strategies/base.py`: `create_signal()`에 `round(x, 8)` 추가 (모든 가격)
  - `PGdb/strategies/pattern_rag_strategy.py`: SL/TP에 `round(x, 8)` 명시적 적용
  - `tradeBot/ai/pattern_rag.py`: `SettingWithCopyWarning` 경고 수정 (`df.copy()` 추가)
  - (이전 수정) `tradeBot/strategies/ict_strategy.py`: 매수/매도 SL/TP에 `round(x, 8)`
  - (이전 수정) `tradeBot/strategies/bollinger_strategy.py`: reversal 모드 SL/TP에 `round(x, 8)`
  - (이전 수정) `tradeBot/ai/pattern_rag.py`: `round(stop_loss, 4)` → `round(stop_loss, 8)`
  - (이전 수정) `tradeBot/static/index.html`: 오더 폼 입력 필드 `step="0.00000001"`
- **검증 방법**: DB에서 1000BONKUSDT/1000PEPEUSDT 신호 조회하여 SL/TP 8자리 확인
- **중요**: 코드 수정 후 **분석 서버(analyzer_server.py) 재시작 필요**

### 2026-01-22: Strategy 컬럼 추가
- 대시보드(index.html)와 신호 페이지(signals.html)에 Strategy 컬럼 추가
- `.badge-strategy`, `.signal-strategy` 스타일 추가 (보라색)
- `/api/signals/current` API 응답에서 `strategy` → `strategy_name`으로 키 변경
- index.html grid-template-columns: 9개 → 10개 컬럼으로 수정

### 2026-01-22: 신호 페이지 "전부" 필터 버그 수정

**문제**: "전부" 필터 선택 시 일부 거래 데이터(`1000BONKUSDT` 등)가 누락됨

**원인**: `signals.html`의 `fetchPositionsForMerge` 함수에서 타임스탬프 변환 문제
```javascript
// 문제 코드
timestamp: new Date(pos.open_time + 'Z').getTime()
```

**해결**: 타임존 표시자가 없을 때만 `Z` 추가 (위 이슈 패턴 #1 참조)

**관련 파일**: `static/signals.html`: 1427-1470 라인 근처

---

---

## 전략 시스템 (Strategy System)

### 전략 파일 위치
- **PGdb/strategies/**: 주요 전략 파일 (실서비스용)
- **tradeBot/strategies/**: 백업/개발용 전략 파일

### 공통 베이스 클래스

**파일**: `PGdb/strategies/base.py`

```python
@dataclass
class TradeSignal:
    """표준화된 거래 신호 데이터 클래스"""
    strategy_name: str      # 전략 이름
    symbol: str             # 심볼
    timeframe: str          # 타임프레임
    signal_type: str        # 'buy' or 'sell'
    entry_price: float      # 진입가
    stop_loss: float        # 손절가
    take_profit_1: float    # 1차 익절가
    take_profit_2: float    # 2차 익절가 (선택)
    take_profit_3: float    # 3차 익절가 (선택)
    confidence: float       # 신뢰도 (0.0 ~ 1.0)
    risk_reward: float      # R/R 비율
    reasons: List[str]      # 신호 근거
    metadata: Dict          # 추가 메타데이터

class BaseStrategy(ABC):
    """모든 전략의 추상 베이스 클래스"""

    # 공통 속성
    name: str               # 전략 이름
    config: Dict            # 설정
    enabled: bool           # 활성화 여부
    use_ai: bool            # AI 검증 사용 여부
    timeframe: str          # 기본 타임프레임
    atr_multiplier: float   # ATR 배수 (SL/TP 계산용)
    min_confidence: float   # 최소 신뢰도

    # 필수 구현 메서드
    @abstractmethod
    def analyze(symbol, timeframe, df) -> List[TradeSignal]

    @abstractmethod
    def get_required_indicators() -> List[str]

    # 공통 유틸리티
    def calculate_atr(df, period=14) -> float
    def calculate_sl_tp(entry, atr, signal_type, multiplier) -> tuple
    def create_signal(...) -> TradeSignal
    def validate_dataframe(df, min_rows=50) -> bool
```

### 전략별 설정 파라미터

**설정 파일**: `PGdb/config.yaml` → `strategies:` 섹션

#### 1. EMA Cross 전략 (ema_cross)
```yaml
ema_cross:
  enabled: true
  use_ai: false
  timeframe: "30m"              # 분석 타임프레임
  htf_timeframes: ["4h", "1d"]  # 상위 타임프레임 (레짐 확인용)
  score_threshold: 0.7          # 최소 점수 (0-1)
  atr_multiplier: 1.5           # ATR 배수
  use_rsi_filter: true          # RSI 필터 사용
  use_vwap_filter: true         # VWAP 필터 사용
```
- **로직**: EMA20/50 골든/데드크로스 + 정배열/역배열 확인
- **신호 조건**:
  - 매수: 골든크로스 + EMA 정배열(20>50>200) + RSI/VWAP 조건
  - 매도: 데드크로스 + EMA 역배열(20<50<200) + RSI/VWAP 조건

#### 2. ICT 전략 (ict)
```yaml
ict:
  enabled: true
  use_ai: true                  # AI 검증 사용
  timeframe: "15m"
  ob_lookback: 20               # Order Block 탐지 범위
  fvg_min_gap_pct: 0.001        # FVG 최소 갭 비율 (0.1%)
  min_confidence: 0.65          # 최소 신뢰도
  min_risk_reward: 1.5          # 최소 R/R 비율
  atr_multiplier: 2.0
  proximity_pct: 0.01           # 가격 근접 비율 (1%)
  use_volume_filter: true       # 볼륨 필터
  use_rsi_filter: true          # RSI 필터
  volume_multiplier: 1.3        # 볼륨 > MA × 1.3
  rsi_overbought: 70            # RSI 과매수
  rsi_oversold: 30              # RSI 과매도
  keltner_multiplier: 2.5       # Keltner Channel ATR 배수
```
- **로직**: Order Block + Fair Value Gap + Confluence
- **익절**: 피보나치 확장 (1.618, 2.618, 4.236)

#### 3. RSI 전략 (rsi)
```yaml
rsi:
  enabled: true
  use_ai: false
  timeframe: "1h"
  overbought: 70                # RSI 과매수 기준
  oversold: 30                  # RSI 과매도 기준
  use_trend_filter: true        # EMA 트렌드 필터
  rsi_period: 14                # RSI 계산 기간
  atr_multiplier: 1.5
  min_confidence: 0.5
```
- **로직**: RSI 과매수/과매도 영역 탈출 시 신호
- **신호 조건**:
  - 매수: RSI가 30 이하에서 30 초과로 상승 (과매도 탈출)
  - 매도: RSI가 70 이상에서 70 미만으로 하락 (과매수 탈출)

#### 4. Bollinger Band 전략 (bollinger)
```yaml
bollinger:
  enabled: true
  use_ai: false
  timeframe: "4h"
  period: 20                    # 볼린저 밴드 기간
  std_dev: 2.0                  # 표준편차 배수
  mode: "breakout"              # 'breakout' 또는 'reversal'
  volume_filter: true           # 거래량 필터
  atr_multiplier: 1.5
  min_confidence: 0.5
```
- **Breakout 모드**: 밴드 돌파 시 추세 추종
  - 상단 돌파 → 매수, 하단 돌파 → 매도
- **Reversal 모드**: 밴드 복귀 시 역추세
  - 상단에서 복귀 → 매도, 하단에서 복귀 → 매수

#### 5. Keltner ICT Turtle 전략 (keltner_ict_turtle)
```yaml
keltner_ict_turtle:
  enabled: true
  use_ai: false
  timeframe: "1h"
  ema_period: 20                # EMA 기간 (Keltner 중심선)
  atr_period: 20                # ATR 기간
  keltner_multiplier: 2.5       # Keltner Channel ATR 배수
  donchian_period: 55           # 돈치안 채널 기간 (터틀 원본)
  ob_lookback: 10               # Order Block 탐색 기간
  fvg_min_gap: 0.001            # FVG 최소 갭
  volume_multiplier: 1.3        # 볼륨 배수
  require_ict: false            # ICT 구조 필수 여부
  min_confidence: 0.5
  atr_multiplier: 2.5
```
- **로직**: Keltner 채널 돌파 + 돈치안 채널 + ICT 구조
- **익절**: 피보나치 기반 (1.618, 2.618)

#### 6. Pattern RAG 전략 (pattern_rag)
```yaml
pattern_rag:
  enabled: true
  use_ai: false                 # 전략 자체가 AI 사용
  timeframe: "1h"
  ollama_url: "http://localhost:11434"
  ollama_model: "qwen2.5:7b-instruct"
  top_k: 10                     # 유사 패턴 검색 개수
  min_confidence: 0.6           # 최소 신뢰도
  min_patterns: 5               # 최소 유사 패턴 수
  auto_learn: true              # 자동 학습
  learn_on_no_patterns: true    # 패턴 없을 때 자동 학습
  atr_multiplier: 2.0
```
- **로직**: 과거 캔들 패턴 학습 + 코사인 유사도 매칭 + AI 분석

#### 7. BB Adaptive RSI 전략 (bb_adaptive_rsi)
```yaml
bb_adaptive_rsi:
  enabled: true
  use_ai: false
  timeframe: "30m"
  bb_length: 20                 # 볼린저 밴드 기간
  bb_std_dev: 3.0               # 표준편차 (3σ)
  er_length: 10                 # Efficiency Ratio 기간
  fast_rsi_length: 2            # 빠른 RSI 기간
  slow_rsi_length: 30           # 느린 RSI 기간
  rsi_smooth: 3                 # RSI 스무딩 기간
  rsi_overbought: 70            # RSI 과매수
  rsi_oversold: 30              # RSI 과매도
  use_rsi_filter: true          # RSI 필터 사용
  atr_multiplier: 1.5
  min_confidence: 0.5
```
- **로직**: 볼린저 밴드 3σ 극단 영역 + Kaufman Efficiency Ratio 기반 Adaptive RSI
- **Adaptive RSI** (Alex Gonzalez):
  - ER = 방향성 변화 / 총 변동성 (0~1)
  - 추세장(ER 높음): 빠른 RSI 가중치 ↑ (민감)
  - 횡보장(ER 낮음): 느린 RSI 가중치 ↑ (둔감)
  - `Adaptive RSI = ER × FastRSI + (1-ER) × SlowRSI`
- **신호 조건**:
  - 롱: BB 하단 3σ 돌파 + RSI 과매도 (또는 RSI 상향 크로스)
  - 숏: BB 상단 3σ 돌파 + RSI 과매수 (또는 RSI 하향 크로스)
- **익절 목표**: BB 중심선 → BB 반대쪽 밴드 근처

### 전략 파라미터 수정 방법

1. **설정 파일 수정**: `PGdb/config.yaml` → `strategies:` 섹션 수정 후 서버 재시작
2. **백테스트 UI**: 전략 설정 버튼 클릭 → 모달에서 파라미터 조정 (실시간 적용)
3. **API 호출**: `POST /api/strategies/{strategy_name}/config`

### 주요 지표 목록

| 전략 | 필요 지표 |
|------|-----------|
| ema_cross | ema20, ema50, ema200, atr, rsi, vwap |
| ict | atr, rsi, volume_ma, keltner_upper, keltner_lower, ema20 |
| rsi | rsi, atr, ema20, ema50 |
| bollinger | bb_upper, bb_mid, bb_lower, atr, rsi |
| keltner_ict_turtle | ema20, atr, rsi, volume_ma, keltner_*, donchian_* |
| pattern_rag | ema20, ema50, ema200, rsi, atr, bb_upper, bb_lower |
| bb_adaptive_rsi | bb_upper_3, bb_lower_3, bb_mid, adaptive_rsi, er, atr |

---

## 이슈 해결 기록

### 2025-01-22: Decimal 타입 에러 수정 및 AI 검증 스위치 추가

#### 문제
백테스트 실행 시 모든 전략에서 `unsupported operand type(s) for /: 'decimal.Decimal' and 'float'` 에러 발생

#### 원인
PostgreSQL DB에서 가져온 OHLCV 데이터가 `decimal.Decimal` 타입으로 반환되는데, pandas/numpy 연산에서 float과 호환되지 않음

#### 해결
모든 전략 파일의 `analyze()` 또는 `_calculate_indicators()` 메서드에 Decimal→float 변환 코드 추가:

```python
# Decimal → float 변환 (DB에서 가져온 데이터 처리)
df = df.copy()
numeric_cols = ['open', 'high', 'low', 'close', 'volume']
for col in numeric_cols:
    if col in df.columns:
        df[col] = df[col].astype(float)
```

**수정된 파일**:
- `strategies/bollinger_strategy.py` - analyze() 메서드
- `strategies/rsi_strategy.py` - analyze() 메서드
- `strategies/ema_cross_strategy.py` - analyze() 메서드
- `strategies/ict_strategy.py` - _calculate_indicators() 메서드
- `strategies/keltner_ict_turtle_strategy.py` - _calculate_indicators() 메서드
- `strategies/pattern_rag_strategy.py` - analyze() 메서드

---

### 2025-01-22: 백테스트 UI AI 검증 스위치 추가

#### 변경 사항
`backtest_web.html`의 모든 전략 설정에 `🤖 AI 검증` 체크박스 추가

#### 전략별 기본값
| 전략 | use_ai 기본값 |
|------|---------------|
| ema_cross | false |
| ict | true |
| rsi | false |
| bollinger | false |
| keltner_ict_turtle | false |
| pattern_rag | false |

#### 사용 방법
1. 전략 옆 ⚙️ 버튼 클릭
2. 모달에서 "🤖 AI 검증" 체크박스 토글
3. 저장 → config.yaml에 반영

#### AI 검증 작동 원리
- `use_ai: true` 설정된 전략의 신호는 `StrategyManager.apply_ai_filter()`에서 Ollama AI로 검증
- AI가 신뢰도를 재평가하고 `min_confidence` 미만 신호 필터링
- 백테스트에서는 AI 검증 시 속도가 느려질 수 있음

---

### 2026-01-22: Pattern RAG 전략 AI 검증 미작동 수정

#### 문제
`pattern_rag` 전략에서 신호가 생성되었으나 AI 검증이 실행되지 않음

#### 원인
`strategy_manager.py`의 `create_strategy_manager()` 함수에서 Pattern RAG 전략이 등록되지 않았음
- ema_cross, ict, rsi, bollinger, keltner_ict_turtle 전략만 등록
- pattern_rag 전략 누락

#### 해결
`tradeBot/strategies/strategy_manager.py`에 Pattern RAG 전략 등록 코드 추가:

```python
# Pattern RAG 전략
if 'pattern_rag' in strategies_config:
    try:
        from pattern_rag_strategy import PatternRAGStrategy
        strategy = PatternRAGStrategy('pattern_rag', strategies_config['pattern_rag'])
        manager.register(strategy)
    except ImportError as e:
        logger.warning(f"Pattern RAG 전략 로드 실패: {e}")
```

#### 주의사항
- Pattern RAG 전략 파일은 `PGdb/strategies/pattern_rag_strategy.py`에 위치
- import 경로 설정을 통해 `PGdb/strategies` 폴더에서 불러옴
- 수정 후 **분석 서버(analyzer_server.py) 재시작 필요**

---

### 2026-01-22: 전략 파일 통합 (중복 제거)

#### 문제
전략 파일들이 두 곳에 중복 존재:
- `PGdb/strategies/` - 원본 파일들
- `tradeBot/strategies/` - 중복 파일들

수정 시 한 곳만 수정하면 다른 파일과 불일치 발생하고, 혼란 야기

#### 해결
**`tradeBot/strategies/` 폴더의 중복 파일들을 모두 아카이브로 이동:**

아카이브 위치: `tradeBot/archive/strategies_backup/`

이동된 파일들:
- `base.py`
- `strategy_manager.py`
- `ema_cross_strategy.py`
- `ict_strategy.py`
- `ict_ai_strategy.py`
- `rsi_strategy.py`
- `bollinger_strategy.py`
- `keltner_ict_turtle_strategy.py`
- `strategy_base.py`
- `base_strategy.py`

**`tradeBot/strategies/`에 남은 파일:**
- `__init__.py` - PGdb/strategies/로 리다이렉트하는 역할만 수행

#### 최종 구조 (⚠️ 중요)

```
PGdb/strategies/              ← 모든 전략 파일 여기에!
├── __init__.py
├── base.py                   ← TradeSignal, BaseStrategy
├── strategy_manager.py       ← StrategyManager, create_strategy_manager
├── ema_cross_strategy.py
├── ict_strategy.py
├── rsi_strategy.py
├── bollinger_strategy.py
├── keltner_ict_turtle_strategy.py
└── pattern_rag_strategy.py

tradeBot/strategies/          ← 리다이렉트만!
└── __init__.py               ← PGdb/strategies/로 리다이렉트
```

#### 사용법
```python
# 두 가지 모두 동일하게 작동
from strategies import BaseStrategy, TradeSignal, create_strategy_manager
from strategies.base import BaseStrategy, TradeSignal
```

#### 주의사항
1. **전략 파일 수정은 반드시 `PGdb/strategies/`에서!**
2. `tradeBot/strategies/__init__.py`는 수정 금지 (리다이렉트 용도)
3. 새 전략 추가 시 `PGdb/strategies/`에 파일 생성
4. 수정 후 **분석 서버 재시작 필요**

---

### 2026-01-22: Signals 페이지에 전략 분석 차트 추가

#### 기능 설명
signals.html 페이지에 **Strategy Analysis** 섹션 추가

- **Symbol Top 10**: 신호 수 기준 상위 10개 심볼 통계
- **Strategy Performance**: 전략별 성과 통계
- **By Month**: 월별 신호 통계
- **By Day of Week**: 요일별 신호 통계
- **By Hour (KST)**: 시간별 신호 통계

각 통계에 포함된 정보:
- 신호 수 (바 차트)
- 승리/패배 수
- 승률 (%)
- 총 PnL

#### 수정 파일
1. **`tradeBot/servers/web_server.py`**
   - 새 API: `GET /api/signals/strategy-analysis`
   - 파라미터: `days` (기본 30), `top_k` (기본 10)
   - 반환: `by_symbol`, `by_strategy`, `by_month`, `by_day`, `by_hour`

2. **`tradeBot/static/signals.html`**
   - Strategy Analysis 섹션 HTML 추가
   - 6열 그리드 CSS 스타일 추가 (`.analysis-row.six-col`)
   - 3열 반응형 레이아웃 (월별/요일별/시간별)
   - JavaScript 함수 추가:
     - `toggleStrategyAnalysis()` - 섹션 토글
     - `loadStrategyAnalysis()` - 데이터 로드
     - `renderSymbolTop10()` - 심볼 Top10 렌더링
     - `renderStrategyPerformance()` - 전략 성과 렌더링
     - `renderMonthAnalysis()` - 월별 렌더링
     - `renderStrategyDayAnalysis()` - 요일별 렌더링
     - `renderStrategyHourAnalysis()` - 시간별 렌더링

#### 사용법
1. Signals 페이지 접속
2. "📊 Strategy Analysis (Top 10)" 헤더 클릭하여 펼치기
3. 30일간의 전략 분석 데이터 자동 로드

---

### 2026-01-22: Strategy Analysis 인터랙티브 필터링 기능 추가

#### 기능 설명
Strategy Analysis 섹션에 인터랙티브 필터링 기능 추가
- 차트 항목 클릭 시 해당 조건으로 다른 모든 차트가 필터링됨
- 복수 필터 조합 지원 (예: "BTCUSDT" + "월요일" + "09시")

#### 필터 유형

1. **소스 필터** (버튼)
   - 전체: 모든 데이터
   - 신호만: 실제 거래가 매칭되지 않은 신호
   - 실제: 실제 거래가 매칭된 신호

2. **클릭 필터** (차트 항목 클릭)
   - Symbol: 특정 심볼만 필터링
   - Strategy: 특정 전략만 필터링
   - Month: 특정 월만 필터링
   - Day of Week: 특정 요일만 필터링
   - Hour: 특정 시간만 필터링

#### 사용법
1. 소스 필터: 상단 버튼 (전체/신호만/실제) 클릭
2. 항목 필터: 차트의 행 클릭 → 다른 차트들 자동 업데이트
3. 같은 항목 다시 클릭 → 필터 해제
4. "필터 초기화" 버튼 → 모든 필터 초기화 (소스 제외)

#### 활성 필터 표시
- 선택된 필터는 상단에 태그로 표시
- 태그의 × 클릭하여 개별 필터 해제
- 선택된 행은 하이라이트 표시 (보라색 테두리)

#### 수정 파일

1. **`tradeBot/servers/web_server.py`**
   - `/api/signals/strategy-analysis` API 필터 파라미터 추가:
     - `source`: 'signal' | 'real' | null
     - `symbol`: 심볼명
     - `strategy`: 전략명
     - `month`: 'YYYY-MM'
     - `day_of_week`: 0-6 (0=Sun)
     - `hour`: 0-23 (KST)
   - 응답에 `active_filters` 추가

2. **`tradeBot/static/signals.html`**
   - CSS: `.analysis-filter-btn`, `.analysis-filter-tag`, `.analysis-row.clickable` 스타일
   - JavaScript:
     - `strategyAnalysisFilters` 상태 객체
     - `toggleAnalysisFilter()` - 소스 필터 토글
     - `applyAnalysisFilter()` - 클릭 필터 적용/해제
     - `clearAnalysisFilters()` - 필터 초기화
     - `renderActiveAnalysisFilters()` - 활성 필터 태그 렌더링
     - 각 render 함수에 클릭 이벤트 및 선택 상태 표시 추가

#### API 사용 예시
```bash
# 기본 (전체)
curl http://localhost:8888/api/signals/strategy-analysis

# BTCUSDT 심볼만
curl http://localhost:8888/api/signals/strategy-analysis?symbol=BTCUSDT

# 실제 거래 + 월요일
curl http://localhost:8888/api/signals/strategy-analysis?source=real&day_of_week=1

# ICT 전략 + 2025년 1월 + 오전 9시
curl http://localhost:8888/api/signals/strategy-analysis?strategy=ict&month=2025-01&hour=9
```

---

### 2026-01-22: Strategy Analysis 소스 필터 데이터 소스 분리

#### 문제
"실제" / "신호만" 필터가 같은 테이블(tradebot_signals)을 조회하여 올바른 데이터를 보여주지 않음

#### 원인
- **실제 거래**: `tradebot_positions` 테이블 (바이낸스 실거래 데이터)
- **신호**: `tradebot_signals` + `tradebot_signal_results` 테이블 (분석 서버 신호 + 시뮬레이션)

두 가지 완전히 다른 데이터 소스인데, 같은 쿼리를 사용했음

#### 해결
`/api/signals/strategy-analysis` API 수정:
- `source='real'`: `tradebot_positions` 테이블 조회
  - pnl > 0 → 승리, pnl <= 0 → 패배
  - strategy_name은 'binance_real'로 고정 (positions에 전략 정보 없음)
- `source='signal'` 또는 없음: `tradebot_signals` + `tradebot_signal_results` 테이블 조회
  - tp1_hit, tp2_hit → 승리, sl_hit → 패배

#### 데이터 구조 차이

| 항목 | 실제 (positions) | 신호 (signals) |
|------|------------------|----------------|
| 테이블 | tradebot_positions | tradebot_signals + tradebot_signal_results |
| 시간 필드 | open_time | created_at |
| 승리 판정 | pnl > 0 | status IN ('tp1_hit', 'tp2_hit') |
| 패배 판정 | pnl <= 0 | status = 'sl_hit' |
| 전략 정보 | 없음 (binance_real) | strategy_name |

---

### 2026-01-22: Strategy Analysis UI 개선

#### 변경 사항

1. **Time Analysis 섹션 삭제**
   - Strategy Analysis와 중복되는 기존 Time Analysis 섹션 제거
   - 관련 JavaScript 함수 삭제 (`toggleAnalysis`, `loadTimeAnalysis`, `renderDayAnalysis`, `renderHourAnalysis`, `refreshTimeAnalysisIfOpen`)

2. **Win/Loss 필터 추가**
   - Strategy Analysis에 "결과" 필터 버튼 추가 (전체/Win/Loss)
   - API에 `result` 파라미터 추가
   - 실제 거래: `pnl > 0` (Win), `pnl <= 0` (Loss)
   - 신호: `status IN ('tp1_hit', 'tp2_hit')` (Win), `status = 'sl_hit'` (Loss)

3. **하단 테이블 필터 연동**
   - Strategy Analysis 필터 변경 시 하단 신호 테이블도 자동 업데이트
   - `syncTableWithAnalysisFilters()` 함수 추가
   - 소스, 심볼, 결과 필터 동기화

#### 수정 파일

1. **`tradeBot/static/signals.html`**
   - Time Analysis HTML 섹션 삭제
   - Win/Loss 필터 버튼 추가
   - `strategyAnalysisFilters.result` 상태 추가
   - `toggleAnalysisFilter()` 함수에 result 필터 처리 추가
   - `syncTableWithAnalysisFilters()` 함수 추가

2. **`tradeBot/servers/web_server.py`**
   - `/api/signals/strategy-analysis` API에 `result` 파라미터 추가
   - positions 쿼리: `pnl > 0` / `pnl <= 0` 조건
   - signals 쿼리: `sr.status` 조건

#### 사용법
1. Strategy Analysis 섹션 펼치기
2. "소스" 버튼으로 데이터 소스 선택 (전체/신호만/실제)
3. "결과" 버튼으로 승패 필터 (전체/Win/Loss)
4. 차트 항목 클릭으로 상세 필터링
5. 필터 변경 시 하단 테이블 자동 업데이트

---

### 2026-01-22: Strategy Analysis Stats 카드 연동 및 Simulate 모달 개선

#### 변경 사항

1. **Stats 카드 라벨 동적 변경**
   - "Total Signals" → source='real' 선택 시 "Total Positions"로 자동 변경
   - `stat-total-label` ID 추가하여 동적 라벨 변경

2. **Stats 카드 데이터 연동**
   - Strategy Analysis 필터 변경 시 Stats 카드도 자동 업데이트
   - API 응답에 `summary` 필드 추가 (total_count, total_wins, total_losses, total_pnl, avg_pnl_percent, win_rate)
   - `updateStatCardsFromSummary()` 함수로 Stats 카드 업데이트

3. **Simulate 버튼 모달 변경**
   - 기존: 필터 영역에 Capital/Risk 입력 필드 + confirm 대화상자
   - 변경: Simulate 버튼 클릭 → 모달 팝업
   - 모달 내용:
     - Capital ($) 입력
     - Risk per Trade (%) 입력
     - "모든 신호 재시뮬레이션" 체크박스 추가
   - **localStorage 연동**: 이전 입력값 자동 저장/복원 (`tradebot_sim_settings`)

4. **update_all 파라미터 추가**
   - `/api/signals/simulate` API에 `update_all` 파라미터 추가
   - `update_all=true`: 이미 시뮬레이션된 신호도 재계산
   - `update_all=false` (기본): pending 상태 신호만 시뮬레이션

#### 수정 파일

1. **`tradeBot/servers/web_server.py`**
   - `/api/signals/strategy-analysis` 응답에 `summary` 필드 추가
   - `/api/signals/simulate` API에 `update_all` 파라미터 추가

2. **`tradeBot/static/signals.html`**
   - Stats 카드: `stat-total-label` ID 추가
   - 필터 영역: Capital/Risk 입력 필드 제거
   - Simulate 모달 HTML 추가 (`#simulate-modal`)
   - JavaScript:
     - `loadSimulationSettings()` - localStorage에서 설정 로드
     - `saveSimulationSettings()` - localStorage에 설정 저장
     - `openSimulateModal()` / `closeSimulateModal()` - 모달 열기/닫기
     - `updateStatCardsFromSummary()` - Stats 카드 업데이트
     - `runSimulation()` 수정 - update_all 파라미터 추가

#### 사용법
1. Simulate 버튼 클릭 → 모달 열림
2. Capital, Risk 설정 (이전 값 자동 복원)
3. "모든 신호 재시뮬레이션" 체크 (선택)
4. "Run Simulation" 클릭
5. 시뮬레이션 완료 후 Stats 카드 및 테이블 자동 업데이트

---

### 2026-01-22: Strategy Analysis 필터 버튼 상태 유지 및 Wins/Losses 계산 수정

#### 문제
1. "실제" 버튼 클릭 시 Total 500인데 Wins 59, Losses 62로 합계가 안 맞음
2. Win/Loss 필터 버튼 클릭 시 버튼 상태가 원위치됨

#### 원인
1. **Wins/Losses 불일치**: positions 테이블에서 `pnl`이 NULL인 레코드가 많음 (379개)
   - 기존: `pnl <= 0`을 losses로 카운트 → NULL은 wins도 losses도 아님
2. **버튼 원위치**: Strategy Analysis 필터 변경 → `syncTableWithAnalysisFilters()` → `applyFilters()` → `loadStats()` 호출
   - `loadStats()`가 기존 API로 Stats 카드를 덮어쓰면서 혼란 발생
   - 버튼 상태 동기화 함수 부재

#### 해결

1. **Wins/Losses 계산 수정** (`web_server.py`)
   ```sql
   -- 변경 전
   COUNT(CASE WHEN p.pnl > 0 THEN 1 END) as wins
   COUNT(CASE WHEN p.pnl <= 0 THEN 1 END) as losses

   -- 변경 후 (pnl IS NOT NULL 조건 추가)
   COUNT(CASE WHEN p.pnl IS NOT NULL AND p.pnl > 0 THEN 1 END) as wins
   COUNT(CASE WHEN p.pnl IS NOT NULL AND p.pnl < 0 THEN 1 END) as losses
   ```
   - `pnl = 0`: wins도 losses도 아님 (무승부)
   - `pnl IS NULL`: 미결 또는 데이터 없음

2. **필터 버튼 상태 동기화** (`signals.html`)
   - `syncFilterButtons()` 함수 추가
   - `strategyAnalysisFilters` 객체와 버튼 상태 동기화
   - `renderActiveAnalysisFilters()` 호출 시 자동 실행

3. **Stats 카드 충돌 방지** (`signals.html`)
   - Strategy Analysis 섹션이 열려있을 때:
     - `applyFilters()` 내 `loadStats()` 호출 건너뛰기
     - `renderBinanceOnlyView()` 내 `updateBinanceStats()` 건너뛰기
   - Stats 카드는 `updateStatCardsFromSummary()`에서만 관리

#### 수정 파일

1. **`tradeBot/servers/web_server.py`**
   - positions wins/losses 계산에 `pnl IS NOT NULL` 조건 추가
   - `pnl < 0`으로 losses 조건 변경 (0은 제외)

2. **`tradeBot/static/signals.html`**
   - `syncFilterButtons()` 함수 추가
   - `applyFilters()`: Strategy Analysis 열려있으면 `loadStats()` 건너뛰기
   - `renderBinanceOnlyView()`: Strategy Analysis 열려있으면 `updateBinanceStats()` 건너뛰기

---

### 2026-01-22: Strategy Analysis 시간대(KST) 수정 및 하단 테이블 필터 연동

#### 문제
1. 테이블에 1월 데이터가 있는데 By Month 분석에서 12월로 표시됨
2. Strategy Analysis 필터 변경 시 하단 테이블이 제대로 필터링되지 않음

#### 원인
1. **시간대 문제**: 월별/요일별 그룹핑 시 UTC 시간 사용 → KST(UTC+9) 변환 누락
   - UTC 12월 31일 15:00 이후 = KST 1월 1일
2. **테이블 필터 불일치**:
   - Strategy Analysis의 "Win"은 tp1_hit + tp2_hit인데, 하단 테이블 Result 필터에는 개별 옵션만 존재
   - "실제" 소스의 Win/Loss 필터가 하단 테이블에 전달되지 않음

#### 해결

1. **시간대 KST 변환 추가** (`web_server.py`)
   - positions 테이블: 월별/요일별 그룹핑 및 필터에 `+ INTERVAL '9 hours'` 추가
   - signals 테이블: 월별/요일별 그룹핑 및 필터에 `+ INTERVAL '9 hours'` 추가
   ```sql
   -- 변경 전
   TO_CHAR(p.open_time, 'YYYY-MM')
   EXTRACT(DOW FROM p.open_time)

   -- 변경 후
   TO_CHAR(p.open_time + INTERVAL '9 hours', 'YYYY-MM')
   EXTRACT(DOW FROM p.open_time + INTERVAL '9 hours')
   ```

2. **Result 필터에 Win/Loss 옵션 추가** (`signals.html`)
   ```html
   <option value="win">Win (TP Hit)</option>
   <option value="loss">Loss (SL Hit)</option>
   ```

3. **signals_repo.py win/loss 필터 지원**
   - `get_signals()`: `result_status='win'` → `sr.status IN ('tp1_hit', 'tp2_hit')`
   - `get_signals()`: `result_status='loss'` → `sr.status = 'sl_hit'`
   - `get_signals_count()`: 동일하게 수정

4. **실제 거래 Win/Loss 필터링** (`signals.html`)
   - `fetchBinanceOnlyWithFilter()` 함수 추가
   - "실제" 소스 선택 시 positions API 조회 후 클라이언트 측에서 Win/Loss 필터링
   - `syncTableWithAnalysisFilters()` 수정: 실제 거래일 때 별도 함수 호출

#### 수정 파일

1. **`tradeBot/servers/web_server.py`**
   - positions/signals 월별/요일별 쿼리에 KST 변환 추가
   - 필터 조건에도 KST 변환 적용

2. **`tradeBot/static/signals.html`**
   - Result 필터에 "Win (TP Hit)", "Loss (SL Hit)" 옵션 추가
   - `fetchBinanceOnlyWithFilter()` 함수 추가
   - `syncTableWithAnalysisFilters()` 수정

3. **`tradeBot/database/signals_repo.py`**
   - `get_signals()`: 'win', 'loss' 필터 처리 추가
   - `get_signals_count()`: 'win', 'loss' 필터 처리 추가

---

### 2026-01-22: 오토봇 신호 테이블 AI Analysis 컬럼 추가

#### 변경 사항

1. **AI Analysis 컬럼 추가** (`index.html`)
   - 신호 테이블에 "AI" 컬럼 추가 (Conf. 컬럼과 R:R 컬럼 사이)
   - `ai_analysis.decision` 값 표시:
     - **APPROVE** → 녹색
     - **REJECT** → 빨간색
     - **CAUTION** → 노란색
     - 없음 → "-" (회색)

2. **R:R 컬럼 간격 조정**
   - R:R 컬럼에 `padding-left:8px` 추가
   - 컬럼 너비: 50px → 60px로 확대

3. **그리드 레이아웃 업데이트**
   - 기존 10컬럼: `90px 70px 60px 90px 90px 90px 70px 50px 110px 80px`
   - 변경 11컬럼: `90px 70px 60px 90px 90px 90px 70px 80px 60px 110px 80px`
   - 새 AI 컬럼: 80px

#### 수정 파일

1. **`tradeBot/static/index.html`**
   - `.table-header`, `.signal-row` CSS grid-template-columns 수정
   - 테이블 헤더에 "AI" 컬럼 추가
   - 신호 행에 AI decision 표시 로직 추가

---

### 2026-01-22: 백테스터 watch_symbols → watched_symbols 테이블 통합

#### 문제
- 백테스터에서 "심볼 추가" 버튼 클릭 시 "심볼 추가 실패" 오류 발생
- 백테스터는 `watch_symbols` 테이블 사용
- 오토봇은 `watched_symbols` 테이블 사용
- 테이블 이름이 달라서 백테스터에서 추가해도 오토봇에서 읽을 수 없었음

#### 해결

1. **테이블 통합** (`backtest_api.py`)
   - 모든 API 엔드포인트가 `watched_symbols` 테이블을 사용하도록 변경
   - 기존 `watch_symbols` 테이블의 스키마와 `watched_symbols` 스키마 매핑:
     - `active` → `enabled`
     - `added_at` → `created_at`
     - `source` → 제거 (오토봇에서 미사용)

2. **마이그레이션 함수 추가** (`migrate_watch_symbols_table()`)
   - 앱 시작 시 자동 실행
   - `watch_symbols` 테이블이 있으면 데이터를 `watched_symbols`로 이동
   - 이동 완료 후 `watch_symbols` 테이블 삭제

3. **스키마 호환성**
   - `watched_symbols` 테이블에 `strategies`, `notes` 컬럼 동적 추가 (없으면)

#### 수정 파일

1. **`PGdb/backtest_api.py`**
   - `get_watch_symbols()`: `watch_symbols` → `watched_symbols`
   - `add_watch_symbols()`: `watched_symbols` 테이블 INSERT로 변경
   - `remove_watch_symbol()`: `watched_symbols` 테이블 UPDATE로 변경
   - `add_backtest_results_to_watch()`: `watched_symbols` 테이블 INSERT로 변경
   - `migrate_watch_symbols_table()`: 마이그레이션 함수 추가
   - 앱 시작 시 마이그레이션 실행

#### 테이블 스키마

**watched_symbols (오토봇 기준)**
```sql
CREATE TABLE watched_symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE,
    timeframe VARCHAR(10) NOT NULL DEFAULT '15m',
    enabled BOOLEAN NOT NULL DEFAULT true,
    strategies VARCHAR(200),  -- 동적 추가
    notes TEXT,                -- 동적 추가
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

### 2026-01-23: 백테스터 전략 병합 기능 수정

#### 문제
- 백테스트 결과에서 심볼을 watched_symbols에 추가할 때 전략이 병합되지 않고 덮어쓰기됨
- 예: 기존에 `ema_cross` 전략이 있는 심볼에 `ict` 전략으로 추가 시, `ict`만 남고 `ema_cross`가 사라짐

#### 원인
- 두 개의 API 엔드포인트가 존재:
  - `/api/backtest/add_to_watch` → `add_backtest_results_to_watch()`
  - `/api/watch_symbols` → `add_watch_symbols()`
- 두 함수 모두 `ON CONFLICT` 시 `EXCLUDED.strategies`로 덮어쓰기

#### 해결

1. **`add_backtest_results_to_watch()` 수정**
   - 기존 심볼 확인 후 전략 병합 (set 연산)
   - `existing_strategies | new_strategies`로 합집합 생성

2. **`add_watch_symbols()` 수정**
   - 동일한 전략 병합 로직 적용
   - 로깅 추가로 병합 과정 확인 가능

#### 수정 파일

1. **`PGdb/backtest_api.py`**
   - `add_backtest_results_to_watch()`: ON CONFLICT 대신 SELECT → UPDATE/INSERT 패턴
   - `add_watch_symbols()`: 동일하게 전략 병합 로직 적용
   ```python
   # 전략 병합 로직
   existing_strategies = set(existing[0].split(',')) if existing[0] else set()
   merged_strategies = existing_strategies | new_strategies
   ```

#### 사용 예시
```
기존: BTCUSDT - strategies: "ema_cross"
추가: BTCUSDT - strategies: "ict"
결과: BTCUSDT - strategies: "ema_cross,ict"
```

---

### 2026-01-23: 트레이드봇 대시보드 스크롤 문제 수정

#### 문제
- 트레이드봇 메인 페이지(`index.html`)에서 스크롤이 되지 않음
- 콘텐츠가 화면을 넘어가도 스크롤바가 나타나지 않음

#### 원인
- `body`에 `overflow: hidden` 설정
- `.center-panel`, `.right-panel`에 `overflow-y: auto` 속성 누락

#### 해결
- `.center-panel`에 `overflow-y: auto` 추가
- `.right-panel`에 `overflow-y: auto` 추가

#### 수정 파일
- **`tradeBot/static/index.html`** - CSS 수정

---

### 2026-01-23: 백테스터 "실시간 분석으로 등록" 버튼 오류 수정

#### 문제
- "실시간 분석으로 등록" 버튼 클릭 시 `timeframe is not defined` 에러 발생

#### 원인
- `timeframe` 변수가 정의되지 않음 (실제 변수명은 `timeframes` 배열)

#### 해결
- `timeframe: timeframe` → `timeframe: timeframes.join(',')` 로 수정
- 선택된 타임프레임 배열을 쉼표 구분 문자열로 변환

#### 수정 파일
- **`PGdb/backtest_web.html`** - 실시간 분석 등록 함수

---

### 2026-01-23: 백테스터 BB Adaptive RSI 설정 폼 추가

#### 문제
- `bb_adaptive_rsi` 전략의 설정 버튼(⚙️)을 클릭해도 아무것도 표시되지 않음
- "이 전략의 설정 폼이 정의되지 않았습니다" 메시지만 표시

#### 원인
- `backtest_web.html`의 `defaultStrategyParams` 객체에 `bb_adaptive_rsi` 항목이 없음

#### 해결
- `defaultStrategyParams`에 `bb_adaptive_rsi` 설정 폼 추가

#### 추가된 설정 항목
| 항목 | 타입 | 범위 | 기본값 |
|------|------|------|--------|
| 🤖 AI 검증 | 체크박스 | - | false |
| 타임프레임 | 선택 | 15m/30m/1h/4h | 30m |
| BB 기간 | 숫자 | 10-50 | 20 |
| BB 표준편차 (σ) | 숫자 | 1-5 | 3.0 |
| ER 기간 | 숫자 | 5-30 | 10 |
| 빠른 RSI 기간 | 숫자 | 2-10 | 2 |
| 느린 RSI 기간 | 숫자 | 10-50 | 30 |
| RSI 스무딩 | 숫자 | 1-10 | 3 |
| RSI 과매수 | 숫자 | 60-90 | 70 |
| RSI 과매도 | 숫자 | 10-40 | 30 |
| RSI 필터 사용 | 체크박스 | - | true |
| ATR 배수 | 숫자 | 0.5-5 | 1.5 |
| 최소 신뢰도 | 숫자 | 0-1 | 0.5 |

#### 수정 파일
- **`PGdb/backtest_web.html`** - `defaultStrategyParams` 객체에 `bb_adaptive_rsi` 추가

---

### 2026-01-23: 백테스터 거래량 상위 심볼 모달 UI 개선

#### 변경 사항

1. **버튼 색상 변경**
   - "상위 10개", "상위 20개", "상위 50개", "상위 100개", "전체 해제" 버튼
   - 기존: 파란색/보라색 (`bg-blue-500`, `bg-purple-500`)
   - 변경: 회색 (`bg-gray-600`, `bg-gray-700`)

2. **"실시간 분석으로 등록" 버튼 추가**
   - 위치: 모달 푸터 왼쪽
   - 색상: 녹색 (`bg-green-600`)
   - 기능:
     - 선택된 심볼들을 `watched_symbols` 테이블에 등록
     - 현재 선택된 전략(체크된 전략들)을 `strategies` 컬럼에 저장
     - 현재 타임프레임 설정값 사용
     - 등록 결과를 alert로 표시

#### 수정 파일
- **`PGdb/backtest_web.html`** - 거래량 상위 심볼 선택 모달

#### 사용법
1. 백테스터에서 "거래량 상위 심볼" 버튼 클릭
2. 원하는 심볼 선택 (상위 10개, 20개 등 버튼 또는 개별 클릭)
3. "실시간 분석으로 등록" 버튼 클릭
4. 선택된 심볼이 오토봇의 watched_symbols에 등록됨

---

### 2026-01-23: BB Adaptive RSI 전략 추가

#### 신규 전략
- **BB 3σ + Adaptive RSI (Alex Gonzalez)** 전략 추가
- 볼린저 밴드 3 표준편차 극단 영역에서 역추세 진입
- Kaufman Efficiency Ratio 기반 동적 RSI로 시장 상태 적응

#### Adaptive RSI 원리
- Kaufman ER = 방향성 변화 / 총 변동성
- 추세장 (ER 높음): 빠른 RSI (2기간) 가중치 상승 → 민감한 반응
- 횡보장 (ER 낮음): 느린 RSI (30기간) 가중치 상승 → 노이즈 필터링
- `Adaptive RSI = ER × FastRSI + (1-ER) × SlowRSI`

#### 신호 조건
- **롱**: BB 하단 3σ 돌파 + RSI 과매도 (또는 RSI 상향 크로스)
- **숏**: BB 상단 3σ 돌파 + RSI 과매수 (또는 RSI 하향 크로스)

#### 추가/수정 파일
1. **`PGdb/strategies/bb_adaptive_rsi_strategy.py`** - 신규 전략 클래스
2. **`PGdb/strategies/__init__.py`** - BBAdaptiveRSIStrategy export 추가
3. **`PGdb/strategies/strategy_manager.py`** - 전략 자동 등록 로직 추가
4. **`PGdb/config.yaml`** - `bb_adaptive_rsi` 설정 섹션 추가
5. **`tradeBot/strategies/__init__.py`** - 공용 전략 리다이렉트 추가

#### 참고: Pine Script 버전
`tradeBot/indicators/Pine/` 폴더에 관련 Pine Script 인디케이터 존재:
- `adaptive_rsi_alex.pine` - Adaptive RSI 단독 인디케이터
- `bb_adaptive_rsi_strategy.pine` - BB + Adaptive RSI 통합 전략

---

### 2026-01-23: AI 거부 신호 저장 및 표시 기능 추가

#### 변경 사항
기존에는 AI가 거부(reject)한 신호는 저장하지 않았으나, 이제 거부된 신호도 DB에 저장하고 대시보드에서 표시

#### 수정 파일

1. **`tradeBot/servers/analyzer_server.py`**
   - AI 거부 신호도 DB에 저장 (텔레그램 알림은 보내지 않음)
   - `should_notify` 플래그 추가로 저장/알림 분리

2. **`tradeBot/static/index.html`**
   - 거부된 신호에 `.rejected` 클래스 추가 (반투명 + 붉은 배경)
   - "Show Rejected" 체크박스 추가 (기본: 체크됨)
   - 체크 해제 시 거부된 신호 숨김

#### 동작 방식
- **AI 승인 (approve)**: DB 저장 + 텔레그램 알림
- **AI 거부 (reject)**: DB 저장만 (알림 없음)
- **AI 검증 없음**: confidence ≥ 0.5이면 저장 + 알림

#### 대시보드 표시
- 거부된 신호: 반투명 + 붉은색 배경
- AI 컬럼에 "REJECT" 빨간색 표시
- "Show Rejected" 체크박스로 표시/숨김 토글

---

### 2026-01-22: 전략 시스템 통합 및 파일 정리

#### 변경 사항
- 전략 파일을 `PGdb/strategies/` 폴더로 일원화 (상단 "전략 시스템" 섹션 참조)
- 중복 파일 아카이브 이동 (`archive/old_indicators/`, `archive/`)
- `backtest_api.py`, `strategy_backtester.py` import 경로 수정

---

## 참고 문서
- `README_SIGNALS.md`: 신호 시스템 설명
- `README_POSITIONS.md`: 포지션 시스템 설명
- `TRADING_SYSTEM_STRUCTURE.md`: 전체 시스템 구조
- `LIVE_TRADING_GUIDE.md`: 라이브 트레이딩 가이드
