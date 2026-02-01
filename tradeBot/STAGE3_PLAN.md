# 3단계 개발 계획: Paper Trading (자동 매매 시뮬레이션)

## 📋 현재 상태

### ✅ 완료된 것 (1-2단계)
- ✅ 전략 생성 및 백테스트 시스템
- ✅ 실시간 신호 생성
- ✅ 사용자 수동 진입/청산 기록
- ✅ 신호-액션 매칭 시스템
- ✅ 타이밍 학습 모델
- ✅ 정책 추천 시스템
- ✅ PaperBroker 기본 구조
- ✅ WebSocket 데이터 피드 기본 구조

### 🔄 다음 단계 (3단계)
웹소켓을 통한 실시간 차트 움직임을 확인하면서 신호가 나왔을 때 자동 매매를 시뮬레이션하고, 실시간으로 검토하면서 신호 및 진입 타이밍을 수정

---

## 🎯 3단계 목표

1. **실시간 차트 모니터링**
   - WebSocket을 통한 실시간 캔들 데이터 수신
   - 차트 움직임 실시간 표시

2. **자동 매매 시뮬레이션**
   - 신호 발생 시 PaperBroker를 통한 자동 주문 시뮬레이션
   - 타이밍/정책 모델 추천을 반영한 진입/청산

3. **실시간 검토 및 수정**
   - 실시간 포지션 모니터링
   - 신호 및 진입 타이밍 조건 수정 기능
   - 사용자 승인 기반 규칙 변경

---

## 📝 개발 항목

### 1. 실시간 차트 UI
**파일**: `static/paper_trading.html`

**기능**:
- 실시간 캔들 차트 표시 (Chart.js 또는 TradingView 라이브러리)
- WebSocket을 통한 실시간 가격 업데이트
- 현재 포지션 표시
- 신호 발생 시 시각적 표시

**API**:
- `/api/paper/positions` - 현재 포지션 조회
- `/api/paper/orders` - 주문 내역 조회
- `/api/paper/stats` - 실시간 통계

### 2. Paper Trading 엔진
**파일**: `services/paper_trading_engine.py`

**기능**:
- 신호 수신 및 처리
- 타이밍/정책 모델 추천 적용
- PaperBroker를 통한 주문 실행
- 실시간 포지션 관리
- 이벤트 로깅

**워크플로우**:
```
신호 발생 
  → 타이밍 모델 추천 확인
  → 정책 모델 추천 확인
  → 사용자 승인 (선택사항)
  → PaperBroker 주문 실행
  → 포지션 모니터링
  → 청산 조건 확인
  → 자동 청산 또는 수동 청산
```

### 3. 실시간 조건 수정 시스템
**파일**: `services/rule_manager.py`

**기능**:
- 실시간 청산 조건 설정 (예: "15캔들 몸통이 50일선과 닿았을 때 청산")
- 조건 추천 시스템 (정책 모델 기반)
- 사용자 승인 기반 규칙 적용
- 규칙 이력 관리

**예시 조건**:
- 가격이 특정 이동평균선에 닿았을 때
- RSI가 특정 수준에 도달했을 때
- 손익이 특정 %에 도달했을 때
- 시간 기반 청산

### 4. WebSocket 통합
**파일**: `servers/paper_trading_server.py` (새로 생성)

**기능**:
- WebSocket을 통한 실시간 데이터 전송
- 차트 업데이트
- 포지션 상태 업데이트
- 신호 알림

### 5. 실시간 모니터링 대시보드
**파일**: `static/paper_trading.html`

**기능**:
- 실시간 차트
- 현재 포지션 목록
- 실시간 PnL
- 신호 발생 알림
- 규칙 수정 UI

---

## 🔧 구현 순서

### Phase 1: 기본 Paper Trading 엔진 (1주)
1. `PaperTradingEngine` 클래스 구현
2. 신호 → 주문 실행 파이프라인
3. 기본 포지션 관리
4. 간단한 UI (포지션 목록만)

### Phase 2: 실시간 차트 통합 (1주)
1. WebSocket 데이터 피드 연동
2. 실시간 차트 표시
3. 신호/포지션 시각화

### Phase 3: 조건 수정 시스템 (1주)
1. 규칙 관리 시스템
2. 조건 추천 기능
3. 사용자 승인 워크플로우

### Phase 4: 모니터링 및 최적화 (1주)
1. 실시간 통계 대시보드
2. 성과 분석
3. 알림 시스템

---

## 📊 데이터 구조

### Paper Trading 이벤트
```python
{
    "event_type": "paper_order" | "paper_fill" | "paper_position_open" | "paper_position_close",
    "timestamp": datetime,
    "symbol": str,
    "signal_id": str,
    "order": Order,
    "position": Position,
    "recommendations": {
        "timing": {...},
        "policy": {...}
    }
}
```

### 규칙 (Rules)
```python
{
    "rule_id": str,
    "symbol": str,
    "condition_type": "price_ma" | "rsi_level" | "pnl_percent" | "time_based",
    "condition_params": {...},
    "action": "close" | "partial_close" | "trailing_stop",
    "approved": bool,
    "created_at": datetime
}
```

---

## 🎨 UI 구성

### Paper Trading 페이지
- **상단**: 실시간 통계 (총 자본, PnL, 승률)
- **중앙**: 실시간 차트 (캔들 + 포지션 표시)
- **하단**: 
  - 활성 포지션 목록
  - 최근 주문 내역
  - 규칙 관리 패널

### 규칙 수정 모달
- 현재 활성 규칙 목록
- 새 규칙 추가
- 조건 추천 받기
- 승인 대기 중인 규칙

---

## 🔗 연동 포인트

### 기존 시스템과의 연동
1. **Signals**: Analyzer Server에서 생성된 신호 수신
2. **Timing Model**: `/api/timing/predict` API 사용
3. **Policy Model**: `/api/policy/recommend` API 사용
4. **Events**: `tradebot_events` 테이블에 이벤트 저장
5. **Positions**: Paper Trading 포지션도 `tradebot_positions`에 저장 (type='paper')

---

## 🚀 시작 방법

### 1단계: Paper Trading 엔진 구현
```python
# services/paper_trading_engine.py
class PaperTradingEngine:
    def __init__(self, broker: PaperBroker, timing_learner, policy_recommender):
        ...
    
    async def process_signal(self, signal):
        # 타이밍/정책 추천 받기
        # 주문 실행
        # 포지션 모니터링
        ...
```

### 2단계: API 엔드포인트 추가
```python
# servers/web_server.py
@app.post("/api/paper/start")
async def start_paper_trading(...):
    # Paper Trading 엔진 시작

@app.get("/api/paper/positions")
async def get_paper_positions(...):
    # 현재 포지션 조회
```

### 3단계: UI 구현
- `static/paper_trading.html` 생성
- 실시간 차트 통합
- 포지션 모니터링

---

## 📈 성공 기준

- ✅ 실시간 신호 수신 및 자동 주문 시뮬레이션
- ✅ 타이밍/정책 모델 추천 반영
- ✅ 실시간 포지션 모니터링
- ✅ 규칙 수정 및 승인 시스템
- ✅ 실시간 차트 표시
- ✅ 성과 추적 및 분석

---

## 💡 다음 단계로 넘어가기 전 체크리스트

- [ ] Paper Trading 엔진 구현 완료
- [ ] 실시간 차트 연동 완료
- [ ] 규칙 수정 시스템 완료
- [ ] 최소 1주일 이상 시뮬레이션 실행
- [ ] 성과 분석 및 개선
- [ ] 사용자 피드백 반영

---

## 🔄 4단계로 넘어가는 조건

3단계가 안정적으로 작동하고 다음 조건을 만족하면 4단계(실전 거래)로 진행:

1. **안정성**: 1개월 이상 오류 없이 실행
2. **성과**: 일관된 수익 패턴 확인
3. **리스크 관리**: 손실 한도 준수
4. **백업 시스템**: 완벽한 백업 및 복구 시스템 구축
