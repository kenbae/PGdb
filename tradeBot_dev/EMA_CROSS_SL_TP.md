# EMA Cross 전략 SL/TP 계산 방식

## 📊 기본 설정

- **ATR 배수 (atr_multiplier)**: 기본값 `1.5`
- **TP/SL 비율 (tp_ratio)**: 기본값 `2.0`
- **진입 가격**: 현재 캔들의 `close` 가격

---

## 🔢 계산 공식

### 기본 변수
```
sl_distance = ATR × atr_multiplier
            = ATR × 1.5 (기본값)
```

---

## 📈 매수 신호 (BUY) - 롱 포지션

### Stop Loss (손절)
```
Stop Loss = Entry Price - sl_distance
          = Entry Price - (ATR × 1.5)
```

**예시**:
- Entry: $3000
- ATR: $20
- sl_distance = $20 × 1.5 = $30
- **Stop Loss = $3000 - $30 = $2970**

### Take Profit 1 (익절 1)
```
Take Profit 1 = Entry Price + (sl_distance × tp_ratio)
              = Entry Price + (ATR × 1.5 × 2.0)
              = Entry Price + (ATR × 3.0)
```

**예시**:
- Entry: $3000
- ATR: $20
- **Take Profit 1 = $3000 + ($20 × 3.0) = $3060**

### Take Profit 2 (익절 2)
```
Take Profit 2 = Entry Price + (sl_distance × tp_ratio × 1.5)
              = Entry Price + (ATR × 1.5 × 2.0 × 1.5)
              = Entry Price + (ATR × 4.5)
```

**예시**:
- Entry: $3000
- ATR: $20
- **Take Profit 2 = $3000 + ($20 × 4.5) = $3090**

### Risk/Reward 비율
- **Risk**: $30 (SL 거리)
- **Reward (TP1)**: $60 (TP1 거리)
- **Risk/Reward = 1:2**

---

## 📉 매도 신호 (SELL) - 숏 포지션

### Stop Loss (손절)
```
Stop Loss = Entry Price + sl_distance
          = Entry Price + (ATR × 1.5)
```

**예시**:
- Entry: $3000
- ATR: $20
- sl_distance = $20 × 1.5 = $30
- **Stop Loss = $3000 + $30 = $3030**

### Take Profit 1 (익절 1)
```
Take Profit 1 = Entry Price - (sl_distance × tp_ratio)
              = Entry Price - (ATR × 1.5 × 2.0)
              = Entry Price - (ATR × 3.0)
```

**예시**:
- Entry: $3000
- ATR: $20
- **Take Profit 1 = $3000 - ($20 × 3.0) = $2940**

### Take Profit 2 (익절 2)
```
Take Profit 2 = Entry Price - (sl_distance × tp_ratio × 1.5)
              = Entry Price - (ATR × 1.5 × 2.0 × 1.5)
              = Entry Price - (ATR × 4.5)
```

**예시**:
- Entry: $3000
- ATR: $20
- **Take Profit 2 = $3000 - ($20 × 4.5) = $2910**

### Risk/Reward 비율
- **Risk**: $30 (SL 거리)
- **Reward (TP1)**: $60 (TP1 거리)
- **Risk/Reward = 1:2**

---

## 📋 요약표

| 항목 | 매수 (BUY) | 매도 (SELL) |
|------|-----------|------------|
| **진입 가격** | 현재 캔들 Close | 현재 캔들 Close |
| **Stop Loss** | Entry - (ATR × 1.5) | Entry + (ATR × 1.5) |
| **Take Profit 1** | Entry + (ATR × 3.0) | Entry - (ATR × 3.0) |
| **Take Profit 2** | Entry + (ATR × 4.5) | Entry - (ATR × 4.5) |
| **Risk/Reward** | 1:2 (TP1 기준) | 1:2 (TP1 기준) |

---

## 💡 실제 예시

### 시나리오: ETHUSDT 매수 신호
- **현재가 (Entry)**: $2,950
- **ATR (14기간)**: $25
- **atr_multiplier**: 1.5

#### 계산
```
sl_distance = $25 × 1.5 = $37.5

Stop Loss = $2,950 - $37.5 = $2,912.5
Take Profit 1 = $2,950 + ($37.5 × 2.0) = $2,950 + $75 = $3,025
Take Profit 2 = $2,950 + ($37.5 × 3.0) = $2,950 + $112.5 = $3,062.5
```

#### 결과
- **손절 거리**: $37.5 (1.27%)
- **익절 1 거리**: $75 (2.54%)
- **익절 2 거리**: $112.5 (3.81%)
- **Risk/Reward**: 1:2 (TP1 기준)

---

## ⚙️ 설정 변경

### config.yaml에서 변경
```yaml
strategies:
  ema_cross:
    atr_multiplier: 1.5  # 이 값을 변경하면 SL/TP 거리 조정
    # 예: 2.0으로 변경하면 SL 거리가 더 넓어짐
```

### 영향
- **atr_multiplier 증가**: SL 거리 증가 → 더 넓은 손절, 더 큰 TP
- **atr_multiplier 감소**: SL 거리 감소 → 더 좁은 손절, 더 작은 TP

---

## 📝 코드 위치

- **계산 로직**: `PGdb/strategies/base.py`의 `calculate_sl_tp()` 메서드
- **전략 설정**: `PGdb/strategies/ema_cross_strategy.py`
- **호출 위치**: `PGdb/strategies/ema_cross_strategy.py`의 `_evaluate_buy_signal()`, `_evaluate_sell_signal()`

---

## 🔍 ATR (Average True Range) 설명

ATR은 변동성을 측정하는 지표로, 최근 14개 캔들의 평균 변동 범위를 나타냅니다.

- **높은 ATR**: 변동성이 크다 → SL/TP 거리가 넓어짐
- **낮은 ATR**: 변동성이 작다 → SL/TP 거리가 좁아짐

EMA Cross 전략은 시장 변동성에 따라 자동으로 SL/TP 거리를 조정합니다.
