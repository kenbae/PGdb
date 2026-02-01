# 적응형 전략 선택 시스템 (Adaptive Strategy Selection System)

## 📋 개요

심볼별로 가장 잘 맞는 전략을 자동으로 선택하고, 성능 변화에 따라 전략을 전환하는 시스템입니다.

### 목표
- **심볼별 최적 전략 자동 선택**: BTCUSDT는 볼린저 밴드, ETHUSDT는 RSI 등
- **성능 기반 자동 전환**: 전략 성능이 저하되면 더 나은 전략으로 전환
- **실시간 모니터링**: 각 전략의 성과를 지속적으로 추적
- **제안 시스템**: 자동 전환 전에 사용자에게 제안 (선택적 자동 승인 가능)

---

## 🏗️ 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│         Adaptive Strategy Manager                        │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Strategy Performance Tracker                    │   │
│  │  - 심볼별 전략별 성과 추적                        │   │
│  │  - 승률, 평균 수익률, 샤프 비율 등 계산            │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Strategy Selector                               │   │
│  │  - 최근 N개 거래 기준 성과 평가                    │   │
│  │  - 통계적 유의성 검증                             │   │
│  │  - 최고 성과 전략 선택                            │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Strategy Switcher                                │   │
│  │  - 성능 저하 감지                                 │   │
│  │  - 전략 전환 제안/자동 실행                        │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Notification System                              │   │
│  │  - 전략 변경 제안 알림                            │   │
│  │  - 성과 리포트                                   │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 📊 성과 지표 (Performance Metrics)

### 1. 기본 지표
- **승률 (Win Rate)**: 최근 N개 거래 중 승리 비율
- **평균 수익률 (Avg Return)**: 거래당 평균 수익률
- **샤프 비율 (Sharpe Ratio)**: 위험 대비 수익률
- **최대 낙폭 (Max Drawdown)**: 최대 손실 구간
- **Profit Factor**: 총 수익 / 총 손실

### 2. 종합 점수 (Composite Score)
```
종합 점수 = (승률 × 0.3) + (평균 수익률 × 0.3) + (샤프 비율 × 0.2) + (Profit Factor × 0.2)
```

### 3. 평가 기간
- **단기 평가**: 최근 10개 거래 (빠른 반응)
- **중기 평가**: 최근 30개 거래 (안정성)
- **장기 평가**: 최근 100개 거래 (신뢰성)

---

## 🔄 전략 전환 로직

### 1. 자동 전환 조건
```python
# 현재 전략의 성과가 다른 전략보다 낮을 때
if current_strategy_score < best_strategy_score * 0.8:  # 20% 이상 낮으면
    if best_strategy_confidence > 0.7:  # 신뢰도 70% 이상
        switch_to_best_strategy()
```

### 2. 전환 제안 조건
```python
# 자동 전환은 하지 않지만 제안
if current_strategy_score < best_strategy_score * 0.9:  # 10% 이상 낮으면
    suggest_strategy_switch(best_strategy)
```

### 3. 전환 방지 조건 (안정성)
```python
# 최근 전환한 경우 일정 기간 대기
if last_switch_time < min_switch_interval:  # 예: 24시간
    return  # 전환하지 않음

# 최소 거래 수 미달 시 전환하지 않음
if current_strategy_trades < min_trades_for_evaluation:  # 예: 10개
    return
```

---

## 💾 데이터베이스 스키마

### 1. strategy_performance 테이블
```sql
CREATE TABLE strategy_performance (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    
    -- 성과 지표 (최근 N개 거래 기준)
    period_type VARCHAR(10),  -- 'short' (10), 'medium' (30), 'long' (100)
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate DECIMAL(5, 2),  -- %
    avg_return DECIMAL(10, 4),  -- %
    total_pnl DECIMAL(20, 8),
    sharpe_ratio DECIMAL(10, 4),
    profit_factor DECIMAL(10, 4),
    max_drawdown DECIMAL(10, 4),  -- %
    composite_score DECIMAL(10, 4),
    
    -- 평가 기간
    evaluation_start TIMESTAMP,
    evaluation_end TIMESTAMP,
    
    -- 메타데이터
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(symbol, strategy_name, timeframe, period_type)
);

CREATE INDEX idx_strategy_performance_symbol ON strategy_performance(symbol);
CREATE INDEX idx_strategy_performance_score ON strategy_performance(composite_score DESC);
```

### 2. strategy_switches 테이블
```sql
CREATE TABLE strategy_switches (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    
    -- 전환 정보
    from_strategy VARCHAR(50),
    to_strategy VARCHAR(50),
    switch_reason TEXT,
    switch_type VARCHAR(20),  -- 'auto', 'manual', 'suggested'
    
    -- 성과 비교
    old_strategy_score DECIMAL(10, 4),
    new_strategy_score DECIMAL(10, 4),
    expected_improvement DECIMAL(10, 4),  -- %
    
    -- 결과 (전환 후 평가)
    actual_improvement DECIMAL(10, 4),  -- 전환 후 실제 개선도
    switch_successful BOOLEAN,  -- 전환이 성공적이었는지
    
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_strategy_switches_symbol ON strategy_switches(symbol);
CREATE INDEX idx_strategy_switches_created ON strategy_switches(created_at DESC);
```

---

## 🔧 구현 단계

### Phase 1: 성과 추적 시스템 (1주)
1. `StrategyPerformanceTracker` 클래스 구현
2. 거래 결과 수집 및 성과 지표 계산
3. DB 저장 로직 구현

### Phase 2: 전략 선택 시스템 (1주)
1. `StrategySelector` 클래스 구현
2. 전략별 성과 비교 및 순위 매기기
3. 최적 전략 선택 로직

### Phase 3: 전환 시스템 (1주)
1. `StrategySwitcher` 클래스 구현
2. 자동/수동 전환 로직
3. 전환 알림 시스템

### Phase 4: 통합 및 테스트 (1주)
1. `RealtimeTradingEngine`과 통합
2. UI 대시보드 추가
3. 백테스트 및 검증

---

## 📝 사용 예시

### 1. 자동 전략 선택
```python
# 시스템이 자동으로 최고 성과 전략 선택
adaptive_manager = AdaptiveStrategyManager(
    symbol='ETHUSDT',
    timeframe='5m',
    auto_switch=True,  # 자동 전환 활성화
    min_confidence=0.7  # 최소 신뢰도 70%
)

# 현재 전략이 성능 저하 시 자동으로 전환
```

### 2. 전략 제안 받기
```python
# 자동 전환은 하지 않지만 제안
adaptive_manager = AdaptiveStrategyManager(
    symbol='ETHUSDT',
    timeframe='5m',
    auto_switch=False,  # 자동 전환 비활성화
    suggest_switches=True  # 제안만 활성화
)

# 제안 받기
suggestion = adaptive_manager.get_strategy_suggestion()
if suggestion:
    print(f"제안: {suggestion['strategy']}로 전환 (예상 개선: {suggestion['improvement']}%)")
    # 사용자가 수동으로 승인
```

### 3. 성과 리포트
```python
# 심볼별 전략 성과 리포트
report = adaptive_manager.get_performance_report('ETHUSDT')
print(f"최고 전략: {report['best_strategy']}")
print(f"현재 전략: {report['current_strategy']}")
print(f"성과 차이: {report['performance_gap']}%")
```

---

## 🎯 향후 확장

1. **머신러닝 기반 예측**: 과거 패턴으로 미래 성과 예측
2. **앙상블 전략**: 여러 전략 조합
3. **시장 상황별 전략**: 변동성, 트렌드 등에 따른 전략 선택
4. **A/B 테스트**: 두 전략을 동시에 실행하고 비교

---

## ⚠️ 주의사항

1. **과최적화 방지**: 너무 자주 전환하지 않도록 최소 간격 설정
2. **통계적 유의성**: 충분한 샘플 수 확보 후 전환
3. **리스크 관리**: 전환 시 기존 포지션 처리 방법 명확히 정의
4. **백테스트 검증**: 실제 전환 전 백테스트로 검증
