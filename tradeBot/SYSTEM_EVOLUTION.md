# 시스템 진화 계획 (1~4단계)

## 핵심 설계 원칙

### 공통 코어 (1~4단계 재사용)
1. **단일 이벤트 스키마**: 모든 단계에서 동일한 이벤트 포맷 사용
   - Candle → Feature → Signal → Decision(진입/청산) → OrderIntent → Order → Fill → Position → Outcome
2. **Broker 어댑터 패턴**: Backtest/Paper/Live가 동일 인터페이스
3. **리플레이 가능**: 모든 이벤트를 저장하여 재생/검증 가능

---

## 1단계: 전략 제작 + 백테스트 + 수동 진입/청산

### 목표
- 전략이 신호만 생성 (진입/청산은 사용자)
- 사용자 액션(진입/청산) 기록
- 신호 + 사용자 행동 + 결과를 같은 키로 조인 가능한 데이터셋 구축

### 데이터 구조

#### `tradebot_events` (append-only 이벤트 로그)
```sql
CREATE TABLE tradebot_events (
    event_id VARCHAR(50) PRIMARY KEY,
    event_type VARCHAR(30) NOT NULL,  -- 'signal', 'user_action', 'order', 'fill', 'position', 'outcome'
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20),
    timeframe VARCHAR(10),
    data JSONB NOT NULL,  -- 이벤트별 상세 데이터
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `tradebot_user_actions` (사용자 진입/청산 기록)
```sql
CREATE TABLE tradebot_user_actions (
    action_id VARCHAR(50) PRIMARY KEY,
    signal_id VARCHAR(50),  -- 연결된 신호 (nullable)
    action_type VARCHAR(20) NOT NULL,  -- 'enter', 'exit', 'modify'
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'buy', 'sell'
    price DECIMAL(20, 8),
    quantity DECIMAL(20, 8),
    reason TEXT,  -- 사용자가 입력한 이유
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### API 엔드포인트
- `POST /api/actions/enter` - 진입 기록
- `POST /api/actions/exit` - 청산 기록
- `GET /api/actions/list` - 액션 목록 조회
- `GET /api/events/list` - 이벤트 로그 조회

### UI 변경
- `signals.html`: 각 신호에 "진입 기록" 버튼 추가
- `positions.html`: 각 포지션에 "청산 기록" 버튼 추가

---

## 2단계: 신호 + 사용자 결과 병합, 타이밍 학습

### 목표
- 신호와 사용자 액션을 병합하여 "언제 들어가고 나오는지" 학습
- 정책 모델 또는 파라미터 추천 시스템 구축

### 데이터 병합
- `signal_id` ↔ `action_id` ↔ `position_id` 연결
- 매칭 규칙: 신호 후 X분 내 첫 진입을 매칭

### 학습 데이터셋
- 입력: 신호 + 시장 상태(캔들/지표)
- 출력: 사용자 진입/청산 타이밍 + 결과

---

## 3단계: 실시간 시뮬레이션 (Paper Trading)

### 목표
- 웹소켓 실시간 차트 기반 자동매매 시뮬레이션
- 사용자 승인 기반 룰 수정

### PaperBroker 구현
- 실시간 체결 흉내 (지정가/시장가, 슬리피지, 수수료)
- 리스크 엔진 (포지션 한도, 일손실 한도)

### 룰 엔진
- DSL/JSON 룰로 저장 가능한 조건
- 룰 변경: 제안 → 승인 → 버전업 → 적용

---

## 4단계: 실매매 + 급변 대응 + 백업

### 목표
- 안정성/리스크/복구 우선

### LiveBroker + 리스크 엔진
- 서킷브레이커 (폭락/폭등 감지 시 즉시 청산/헤지)
- 포지션 한도, 일손실 한도

### 백업 시스템
- Watchdog 프로세스 (하트비트 체크)
- Order 서버 이중화 (Active/Standby)
- Idempotent 키로 중복 주문 방지

---

## 구현 순서

1. ✅ 이벤트 스키마/DB 테이블 고정
2. ✅ Broker 어댑터 인터페이스 고정
3. ✅ 리플레이(저장→재생) 구현
4. ✅ 그 위에 전략/룰/학습 얹기
