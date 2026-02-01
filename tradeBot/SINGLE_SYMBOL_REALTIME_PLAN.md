# 단일 심볼 실시간 트레이딩 봇 구현 계획

## 🎯 목표

심볼과 전략을 선택한 후, 실시간으로 캔들을 모니터링하면서 전략을 실행하고, 신호가 발생하면 즉시 진입하는 시스템

---

## 💡 핵심 기능

### 1. 실시간 모니터링
- 선택한 심볼의 실시간 캔들 데이터 수신 (WebSocket)
- 선택한 전략을 실시간으로 실행
- 전략 실행 과정을 실시간으로 로그 표시

### 2. 자동 진입
- 전략이 신호를 생성하면 즉시 자동 진입
- 진입 후 실시간 가격, PnL, SL/TP 모니터링

### 3. 실시간 UI
- 모니터링 상태 표시
- 전략 실행 로그
- 현재 포지션 정보 (가격, PnL, SL/TP)

---

## 📝 구현 계획

### 1. RealtimeTradingEngine 구현
**파일**: `services/realtime_trading_engine.py`

**기능**:
- 실시간 캔들 데이터 수신 (WebSocket)
- 선택한 전략 실행
- 신호 생성 시 즉시 진입
- 실시간 포지션 모니터링

**워크플로우**:
```
1. 심볼/전략 선택 후 "실시간 모니터링" 시작
2. WebSocket으로 실시간 캔들 데이터 수신
3. 캔들이 완성될 때마다 전략 실행
4. 전략이 신호 생성 → 즉시 진입
5. 진입 후 실시간 가격 업데이트 및 PnL 계산
6. SL/TP 도달 시 자동 청산
```

---

### 2. 실시간 캔들 WebSocket 연결
**파일**: `core/websocket_feed.py` 또는 새로 구현

**기능**:
- Binance WebSocket으로 실시간 캔들 데이터 수신
- 캔들 완성 이벤트 발생
- 실시간 가격 업데이트

---

### 3. 실시간 모니터링 UI
**파일**: `static/realtime_trading.html` (새 파일)

**UI 구성**:
```
┌─────────────────────────────────────────┐
│  실시간 모니터링                        │
│  [중지]                                 │
├─────────────────────────────────────────┤
│  설정                                   │
│  심볼: [BTCUSDT ▼]                      │
│  전략: [EMA Cross ▼]                    │
│  타임프레임: [5m ▼]                     │
│  [실시간 모니터링 시작]                 │
├─────────────────────────────────────────┤
│  모니터링 상태                          │
│  ┌─────────────────────────────────┐   │
│  │ TF 5m EMA 전략 스캔 중...        │   │
│  │ 캔들 close 대기 중... 4:15초    │   │
│  │ 신호 포착! 진입!                 │   │
│  │ 진입 완료                        │   │
│  └─────────────────────────────────┘   │
├─────────────────────────────────────────┤
│  현재 포지션                             │
│  심볼: BTCUSDT                          │
│  방향: LONG                             │
│  진입가: $50,000.00                     │
│  현재가: $50,100.00                     │
│  PnL: +$100.00 (+0.20%)                │
│  SL: $49,000.00 (-2.00%)               │
│  TP: $51,000.00 (+2.00%)               │
└─────────────────────────────────────────┘
```

---

### 4. API 엔드포인트
**파일**: `servers/web_server.py`

**엔드포인트**:
```python
POST /api/realtime/start          # 실시간 모니터링 시작
POST /api/realtime/stop           # 실시간 모니터링 중지
GET  /api/realtime/status         # 모니터링 상태 조회
GET  /api/realtime/logs           # 실시간 로그 조회
POST /api/realtime/update-price   # 가격 업데이트 (WebSocket)
```

---

## 🔄 워크플로우 상세

### 시작 단계
1. 사용자가 심볼, 전략, 타임프레임 선택
2. "실시간 모니터링 시작" 버튼 클릭
3. `RealtimeTradingEngine` 생성 및 시작
4. WebSocket으로 실시간 캔들 데이터 수신 시작

### 모니터링 단계
1. 캔들 데이터 수신
2. 캔들이 완성될 때마다 전략 실행
3. 전략 실행 과정을 로그로 표시:
   - "TF 5m EMA 전략 스캔 중..."
   - "캔들 close 대기 중... 4:15초"
   - "신호 포착! 진입!"

### 진입 단계
1. 전략이 신호 생성
2. 즉시 PaperBroker로 주문 실행
3. 진입 완료 로그 표시
4. 실시간 가격 모니터링 시작

### 모니터링 단계 (진입 후)
1. 실시간 가격 업데이트 (WebSocket)
2. PnL 계산 및 표시
3. SL/TP 가격 및 % 표시
4. SL/TP 도달 시 자동 청산

---

## 📊 구현 세부사항

### RealtimeTradingEngine 클래스
```python
class RealtimeTradingEngine:
    def __init__(
        self,
        symbol: str,
        strategy_name: str,
        timeframe: str,
        broker: PaperBroker,
        strategy_manager,
        on_log_update: Callable,  # 로그 업데이트 콜백
        on_position_update: Callable  # 포지션 업데이트 콜백
    ):
        self.symbol = symbol
        self.strategy_name = strategy_name
        self.timeframe = timeframe
        self.broker = broker
        self.strategy = strategy_manager.get_strategy(strategy_name)
        self.on_log_update = on_log_update
        self.on_position_update = on_position_update
        
        # 캔들 데이터 저장
        self.candles = []  # 최근 캔들들
        self.current_candle = None  # 현재 진행 중인 캔들
        
        # WebSocket 연결
        self.ws_connection = None
        
    async def start(self):
        """실시간 모니터링 시작"""
        # WebSocket 연결
        await self._connect_websocket()
        
        # 캔들 수신 루프 시작
        asyncio.create_task(self._candle_monitoring_loop())
        
    async def _connect_websocket(self):
        """Binance WebSocket 연결"""
        # 실시간 캔들 데이터 수신
        
    async def _candle_monitoring_loop(self):
        """캔들 모니터링 루프"""
        while self.is_running:
            # 캔들 데이터 수신
            # 캔들 완성 체크
            # 전략 실행
            # 신호 처리
            
    async def _run_strategy(self, candles):
        """전략 실행"""
        # 전략에 캔들 데이터 전달
        # 신호 생성 확인
        # 신호가 있으면 진입
        
    async def _enter_position(self, signal):
        """포지션 진입"""
        # PaperBroker로 주문 실행
        # 로그 업데이트
```

---

## ✅ 구현 체크리스트

### Backend
- [ ] `RealtimeTradingEngine` 클래스 구현
- [ ] 실시간 캔들 WebSocket 연결
- [ ] 전략 실행 로직
- [ ] 신호 생성 시 즉시 진입
- [ ] 실시간 가격 업데이트
- [ ] 로그 시스템
- [ ] API 엔드포인트 추가

### Frontend
- [ ] `realtime_trading.html` 페이지 생성
- [ ] 심볼/전략/타임프레임 선택 UI
- [ ] 실시간 모니터링 로그 표시
- [ ] 현재 포지션 정보 표시
- [ ] WebSocket 연결 및 실시간 업데이트

---

## 🚀 구현 순서

1. **RealtimeTradingEngine 기본 구조** (1시간)
   - 클래스 정의
   - 기본 워크플로우

2. **실시간 캔들 WebSocket** (1시간)
   - Binance WebSocket 연결
   - 캔들 데이터 수신 및 파싱

3. **전략 실행 통합** (1시간)
   - 전략 매니저와 연동
   - 캔들 완성 시 전략 실행

4. **신호 처리 및 진입** (1시간)
   - 신호 생성 시 즉시 진입
   - PaperBroker 연동

5. **실시간 UI** (1시간)
   - HTML 페이지 생성
   - 로그 표시
   - 포지션 정보 표시
   - WebSocket 연결

6. **API 엔드포인트** (30분)
   - 시작/중지/상태 조회
   - 로그 조회

**총 예상 시간: 약 5-6시간**

---

## 💡 추가 고려사항

1. **캔들 완성 감지**
   - Binance WebSocket에서 캔들 완성 이벤트 감지
   - 또는 주기적으로 캔들 상태 확인

2. **전략 실행 타이밍**
   - 캔들이 완성된 직후 전략 실행
   - 또는 일정 간격으로 실행

3. **에러 처리**
   - WebSocket 연결 끊김 시 재연결
   - 전략 실행 실패 시 처리

4. **성능 최적화**
   - 불필요한 전략 실행 방지
   - 효율적인 데이터 구조

---

**이 방식으로 구현하면 실시간으로 캔들을 모니터링하면서 전략을 실행하고, 신호가 발생하면 즉시 진입하는 시스템을 만들 수 있습니다!**
