# EMA Cross 전략 청산 규칙

## 📋 개요

EMA Cross 전략의 고급 청산 규칙:
1. **TP1 도달 시 N% 부분 청산**
2. **TP2 도달 시 SL을 본절(진입가)로 이동** (트레일링 스톱)
3. **EMA50 터치 시 익절**

---

## 🔄 청산 순서

### 1단계: TP1 도달 → 부분 청산

**조건**: 가격이 TP1에 도달

**동작**:
- 포지션의 **N% 부분 청산** (기본값: 50%)
- 나머지 포지션은 계속 유지

**예시**:
- 진입 수량: 1.0 ETH
- TP1 도달 → 0.5 ETH 부분 청산 (50%)
- 남은 수량: 0.5 ETH

---

### 2단계: TP2 도달 → 트레일링 스톱 활성화

**조건**: 가격이 TP2에 도달

**동작**:
- **Stop Loss를 본절(진입가)로 이동**
- 이후 가격이 진입가 아래로 떨어지면 전체 청산

**예시**:
- 진입가: $2,950
- TP2 도달 → SL을 $2,950으로 이동
- 가격이 $2,950 아래로 떨어지면 → 전체 청산 (익절)

---

### 3단계: EMA50 터치 → 익절

**조건**: 캔들 몸통이 EMA50에 닿음

**동작**:
- **전체 포지션 익절**
- TP1/TP2 도달 여부와 관계없이 청산

**예시**:
- 현재가: $2,960
- EMA50: $2,955
- 캔들 몸통이 EMA50에 닿음 → 전체 청산

---

## 📊 청산 우선순위

1. **TP1 부분 청산** (가장 먼저)
2. **TP2 트레일링 스톱** (TP1 이후)
3. **EMA50 터치** (언제든지)
4. **기본 SL** (TP2 도달 전까지만)

---

## ⚙️ 설정 파라미터

### EMACrossExitManager 초기화

```python
exit_manager = EMACrossExitManager(
    symbol='ETHUSDT',
    timeframe='5m',
    tp1_partial_exit_pct=50.0,  # TP1에서 50% 부분 청산
    use_trailing_stop=True,  # TP2 도달 후 트레일링 스톱
    trailing_stop_atr_multiplier=1.0,  # 트레일링 스톱 ATR 배수
    exit_on_ema50_touch=True  # EMA50 터치 시 청산
)
```

### 파라미터 설명

- **tp1_partial_exit_pct**: TP1 도달 시 부분 청산 비율 (0-100)
  - `50.0`: 50% 부분 청산
  - `0.0`: 부분 청산 안 함 (전체 유지)
  - `100.0`: TP1 도달 시 전체 청산

- **use_trailing_stop**: TP2 도달 후 트레일링 스톱 사용 여부
  - `True`: TP2 도달 시 SL을 본절로 이동
  - `False`: 트레일링 스톱 사용 안 함

- **trailing_stop_atr_multiplier**: 트레일링 스톱 ATR 배수 (현재 미사용, 향후 확장용)
  - 기본값: 1.0

- **exit_on_ema50_touch**: EMA50 터치 시 청산 여부
  - `True`: EMA50 터치 시 익절
  - `False`: EMA50 터치 무시

---

## 🔍 실제 작동 예시

### 시나리오: ETHUSDT 롱 포지션

**진입**:
- Entry: $2,950
- SL: $2,912.5 (ATR × 1.5)
- TP1: $3,025 (ATR × 3.0)
- TP2: $3,062.5 (ATR × 4.5)
- 수량: 1.0 ETH

**진행**:

1. **가격이 $3,025 도달** (TP1)
   - ✅ 0.5 ETH 부분 청산 (50%)
   - 남은 수량: 0.5 ETH
   - 부분 청산 PnL: ($3,025 - $2,950) × 0.5 = $37.5

2. **가격이 $3,062.5 도달** (TP2)
   - ✅ SL을 $2,950 (본절)로 이동
   - 트레일링 스톱 활성화

3. **가격이 $2,960까지 상승 후 $2,955로 하락**
   - EMA50: $2,955
   - 캔들 몸통이 EMA50에 닿음
   - ✅ 나머지 0.5 ETH 전체 청산 (EMA50 터치)
   - 청산 PnL: ($2,955 - $2,950) × 0.5 = $2.5

**총 PnL**: $37.5 + $2.5 = $40.0

---

## 🧪 백테스터에서 테스트

### 사용 방법

```python
from strategy_backtester import StrategyBacktester

backtester = StrategyBacktester()

result = backtester.backtest(
    strategy_name='ema_cross',  # EMA Cross 전략
    symbol='ETHUSDT',
    tf='5m',
    start_date=datetime(2025, 1, 1),
    end_date=datetime(2025, 1, 25),
    initial_capital=10000.0,
    risk_pct=2.0
)

print(f"총 거래 수: {result.total_trades}")
print(f"승률: {result.win_rate:.2f}%")
print(f"총 수익: {result.total_pnl:.2f} USDT")
```

### 백테스터 청산 로직

백테스터는 자동으로 다음 청산 규칙을 적용합니다:

1. **TP1 부분 청산**: TP1 도달 시 50% 부분 청산
2. **TP2 트레일링 스톱**: TP2 도달 시 SL을 본절로 이동
3. **EMA50 터치**: EMA50 터치 시 전체 청산

---

## 📝 코드 위치

- **실시간 트레이딩**: `PGdb/tradeBot_dev/services/ema_cross_exit_manager.py`
- **백테스터**: `PGdb/strategy_backtester.py`의 `_check_ema_cross_exit()` 메서드
- **통합**: `PGdb/tradeBot_dev/services/realtime_trading_engine.py`

---

## ⚠️ 주의사항

1. **부분 청산**: 현재 PaperBroker는 부분 청산을 직접 지원하지 않으므로, 실시간 트레이딩에서는 전체 청산으로 처리됩니다. 향후 부분 청산 기능 추가 예정.

2. **트레일링 스톱**: TP2 도달 후 SL을 본절로 이동하므로, 이후 하락 시에도 손실 없이 청산됩니다.

3. **EMA50 터치**: 캔들 몸통(open/close 사이)이 EMA50에 닿으면 청산됩니다.

---

## 🎯 설정 변경 예시

### TP1에서 30%만 부분 청산
```python
tp1_partial_exit_pct=30.0
```

### TP1에서 전체 청산 (부분 청산 안 함)
```python
tp1_partial_exit_pct=100.0
```

### 트레일링 스톱 비활성화
```python
use_trailing_stop=False
```

### EMA50 터치 청산 비활성화
```python
exit_on_ema50_touch=False
```
