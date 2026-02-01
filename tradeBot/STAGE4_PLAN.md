# 4단계 개발 계획: Live Trading (실전 자동매매)

## 📋 현재 상태

### ✅ 완료된 것 (1-3단계)
- ✅ 전략 생성 및 백테스트 시스템
- ✅ 실시간 신호 생성
- ✅ 사용자 수동 진입/청산 기록
- ✅ 신호-액션 매칭 시스템
- ✅ 타이밍 학습 모델
- ✅ 정책 추천 시스템
- ✅ Paper Trading (시뮬레이션) 완료
- ✅ BaseBroker 인터페이스 정의
- ✅ PaperBroker 구현 완료

### 🔄 다음 단계 (4단계)
실전 거래소(Binance)와 연동하여 실제 자금으로 자동매매를 실행하되, 안전장치와 백업 시스템을 완벽히 구축

---

## 🎯 4단계 목표

1. **LiveBroker 구현**
   - BaseBroker 인터페이스를 상속받아 Binance 실전 거래소 연동
   - 실제 주문 실행 (시장가/지정가)
   - 실시간 포지션 추적
   - SL/TP 자동 실행

2. **리스크 관리 시스템**
   - 서킷브레이커 (급격한 가격 변동 감지 시 즉시 청산)
   - 포지션 한도 관리
   - 일일 손실 한도 관리
   - 긴급 정지 기능

3. **백업 및 안정성 시스템**
   - Watchdog 프로세스 (하트비트 체크)
   - Order 서버 이중화 (Active/Standby)
   - Idempotent 키로 중복 주문 방지
   - 모든 거래 이벤트 로깅

4. **Live Trading UI**
   - 실전 거래 모니터링 페이지
   - 실시간 포지션 및 PnL 표시
   - 긴급 정지 버튼
   - 리스크 지표 표시

---

## 📝 개발 항목

### 1. LiveBroker 구현
**파일**: `brokers/live_broker.py`

**기능**:
- `BaseBroker` 인터페이스 구현
- Binance API (CCXT) 연동
- 실제 주문 제출/취소/조회
- 실시간 포지션 상태 업데이트
- SL/TP 주문 자동 설정
- 리스크 한도 체크

**주요 메서드**:
```python
class LiveBroker(BaseBroker):
    def submit_order(self, intent: OrderIntent) -> Order
    def cancel_order(self, order_id: str) -> bool
    def get_market_price(self, symbol: str) -> float
    def update_positions(self, current_prices: Dict[str, float]) -> List[Position]
    def get_open_positions(self) -> List[Position]
    def _check_risk_limits(self) -> bool
    def _circuit_breaker_check(self, symbol: str, price: float) -> bool
```

**안전장치**:
- 최대 포지션 수 제한 (기본: 2개)
- 일일 손실 한도 (기본: 2%)
- 긴급 정지 플래그
- 모든 주문 전 리스크 체크

---

### 2. 리스크 관리 엔진
**파일**: `services/risk_manager.py`

**기능**:
- 서킷브레이커 (가격 급변 감지)
  - 5분 내 ±5% 이상 변동 시 경고
  - 5분 내 ±10% 이상 변동 시 자동 청산
- 포지션 한도 관리
  - 최대 동시 포지션 수 체크
  - 심볼별 포지션 중복 방지
- 일일 손실 한도
  - 일일 손실이 한도 도달 시 거래 중단
  - 다음날 자동 리셋
- 긴급 정지
  - 사용자 또는 시스템이 트리거 가능
  - 모든 오픈 포지션 즉시 청산

**서킷브레이커 로직**:
```python
def check_circuit_breaker(symbol: str, current_price: float, price_history: List[float]) -> bool:
    """
    가격 급변 감지
    
    Returns:
        True: 정상, False: 긴급 청산 필요
    """
    if len(price_history) < 10:
        return True
    
    # 최근 5분간 가격 변동률 계산
    price_5min_ago = price_history[-10]  # 10개 전 = 약 5분 전
    change_pct = abs((current_price - price_5min_ago) / price_5min_ago) * 100
    
    if change_pct >= 10:
        logger.critical(f"🚨 서킷브레이커 트리거: {symbol} {change_pct:.2f}% 변동")
        return False  # 긴급 청산
    elif change_pct >= 5:
        logger.warning(f"⚠️ 서킷브레이커 경고: {symbol} {change_pct:.2f}% 변동")
    
    return True
```

---

### 3. Live Trading Engine
**파일**: `services/live_trading_engine.py`

**기능**:
- PaperTradingEngine과 유사하지만 LiveBroker 사용
- 신호 수신 및 처리
- 타이밍/정책 모델 추천 적용
- 리스크 매니저와 연동
- 모든 거래 이벤트 로깅

**워크플로우**:
```
신호 발생
  → 리스크 체크 (포지션 한도, 일일 손실)
  → 서킷브레이커 체크
  → 타이밍 모델 추천 확인
  → LiveBroker로 주문 제출
  → 주문 상태 모니터링
  → SL/TP 자동 설정
  → 포지션 추적
  → 이벤트 로깅
```

---

### 4. 백업 시스템

#### 4.1 Watchdog 프로세스
**파일**: `services/watchdog.py`

**기능**:
- Order 서버 하트비트 체크 (30초마다)
- 서버 응답 없으면 Standby 서버로 전환
- 프로세스 크래시 감지 및 재시작
- Telegram 알림

#### 4.2 Idempotent 키 시스템
**파일**: `services/idempotent_manager.py`

**기능**:
- 모든 주문에 고유 ID 부여
- 중복 주문 방지 (같은 ID로 재시도 시 무시)
- DB에 주문 ID 저장 및 체크

**구현**:
```python
class IdempotentManager:
    def generate_order_id(self, signal_id: str, timestamp: int) -> str:
        """고유 주문 ID 생성"""
        return f"{signal_id}_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    def is_duplicate(self, order_id: str) -> bool:
        """중복 주문 체크"""
        # DB에서 order_id 조회
        return self.db.has_order_id(order_id)
```

#### 4.3 Order 서버 이중화
**파일**: `servers/order_server_standby.py`

**기능**:
- Active 서버와 동일한 기능
- Active 서버 장애 시 자동 전환
- 상태 동기화

---

### 5. Live Trading API
**파일**: `servers/web_server.py` (추가 엔드포인트)

**엔드포인트**:
```python
POST /api/live/start          # Live Trading 시작
POST /api/live/stop           # Live Trading 중지
GET  /api/live/status         # Live Trading 상태 조회
POST /api/live/emergency-stop # 긴급 정지
GET  /api/live/positions      # 실전 포지션 조회
GET  /api/live/risk-status    # 리스크 상태 조회
```

---

### 6. Live Trading UI
**파일**: `static/live_trading.html`

**기능**:
- 실전 포지션 실시간 모니터링
- 실시간 PnL 및 통계
- 리스크 지표 표시
  - 일일 손실률
  - 포지션 수 / 최대 포지션 수
  - 서킷브레이커 상태
- 긴급 정지 버튼
- 주문 내역 표시
- WebSocket 실시간 업데이트

**UI 구성**:
```
┌─────────────────────────────────────┐
│  Live Trading Status                │
│  [실행 중] [긴급 정지]              │
├─────────────────────────────────────┤
│  리스크 지표                        │
│  일일 손실: -$50 (-0.5%)           │
│  포지션: 2/2                        │
│  서킷브레이커: 정상                 │
├─────────────────────────────────────┤
│  실전 포지션                        │
│  [테이블: Symbol, Side, Entry, ...] │
├─────────────────────────────────────┤
│  최근 주문                          │
│  [테이블: Order ID, Symbol, ...]   │
└─────────────────────────────────────┘
```

---

## 🔒 안전장치 체크리스트

### 필수 안전장치:
- [ ] API 키 권한: 출금 비활성화
- [ ] IP 제한 설정
- [ ] 2FA 인증
- [ ] 최대 포지션 수 제한
- [ ] 일일 손실 한도
- [ ] 서킷브레이커
- [ ] 긴급 정지 기능
- [ ] 모든 거래 로깅
- [ ] Idempotent 키 (중복 주문 방지)
- [ ] Watchdog 프로세스

### 권장 안전장치:
- [ ] 초기 자본 소액 ($100-$500)
- [ ] 레버리지 제한 (1-3배)
- [ ] 거래당 리스크 제한 (1-2%)
- [ ] Order 서버 이중화
- [ ] 정기 백업

---

## 📊 개발 순서

### Phase 1: LiveBroker 구현 (1-2일)
1. `brokers/live_broker.py` 작성
2. BaseBroker 인터페이스 구현
3. Binance API 연동 테스트
4. 기본 주문 실행 테스트

### Phase 2: 리스크 관리 (1일)
1. `services/risk_manager.py` 작성
2. 서킷브레이커 구현
3. 포지션/손실 한도 체크
4. 긴급 정지 기능

### Phase 3: Live Trading Engine (1일)
1. `services/live_trading_engine.py` 작성
2. PaperTradingEngine 구조 참고
3. LiveBroker 연동
4. 리스크 매니저 통합

### Phase 4: 백업 시스템 (1-2일)
1. Idempotent 키 시스템
2. Watchdog 프로세스
3. Order 서버 이중화 (선택사항)

### Phase 5: API 및 UI (1일)
1. API 엔드포인트 추가
2. `live_trading.html` 작성
3. WebSocket 통합
4. 테스트

---

## ⚠️ 주의사항

### 실전 거래 전 필수 확인:
1. **소액으로 시작**: $100-$500
2. **API 키 안전 설정**: 출금 비활성화, IP 제한
3. **테스트넷에서 충분한 테스트**
4. **모든 안전장치 활성화 확인**
5. **긴급 정지 방법 숙지**

### 개발 중:
- 실전 API 키는 절대 커밋하지 않기
- `.env` 파일에 저장
- 테스트넷으로 먼저 검증
- 로깅 철저히

---

## 🚀 시작하기

### 1. 환경 변수 설정
```env
# .env
BINANCE_LIVE_API_KEY=your_api_key
BINANCE_LIVE_API_SECRET=your_api_secret
LIVE_INITIAL_CAPITAL=100
LIVE_MAX_POSITIONS=2
LIVE_MAX_DAILY_LOSS=0.02
LIVE_MAX_LEVERAGE=3
```

### 2. LiveBroker 구현 시작
```bash
# brokers/live_broker.py 생성
# BaseBroker 상속
# Binance API 연동
```

### 3. 테스트넷에서 검증
```bash
# 테스트넷 API 키로 먼저 테스트
# 모든 기능 정상 작동 확인
```

---

## 📈 다음 단계

4단계 완료 후:
- 실전 거래 모니터링
- 성과 분석 및 최적화
- 전략 개선
- 자본 증액 (성과 확인 후)

---

**⚠️ 실전 거래는 신중하게! 모든 안전장치를 확인하고 소액으로 시작하세요.**
