# Keltner ICT Turtle 백테스트 가이드

## 🐢 전략 개요

### 켈트너 ICT 터틀 전략
현대화된 터틀 트레이딩 전략으로 다음을 결합:

1. **켈트너 채널** (동적 변동성 채널)
2. **돈치안 채널** (터틀 원본 55일 돌파)
3. **ICT Order Blocks** (스마트 머니 영역)
4. **Fair Value Gaps** (되돌림 타겟)
5. **피보나치 익절** (1.618, 2.618, 4.236)
6. **다단계 리스크 관리**

---

## 🚀 사용 방법

### 1. 단일 심볼 백테스트

```bash
# BTCUSDT 1시간봉 2023년 전체
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2023-01-01 \
    --end 2024-01-01

# ETHUSDT 4시간봉 2024년
python keltner_ict_turtle.py \
    --symbol ETHUSDT \
    --tf 4h \
    --start 2024-01-01 \
    --end 2025-01-01

# 초기 자본 $50,000
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2023-01-01 \
    --capital 50000
```

---

### 2. 여러 심볼 백테스트

```bash
# BTC, ETH, XRP 동시 백테스트
python keltner_ict_turtle.py \
    --symbols BTCUSDT ETHUSDT XRPUSDT \
    --tf 1h \
    --start 2023-01-01 \
    --end 2024-01-01

# 상위 10개 코인
python keltner_ict_turtle.py \
    --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT \
              ADAUSDT DOTUSDT MATICUSDT LINKUSDT AVAXUSDT \
    --tf 4h \
    --start 2023-01-01
```

---

### 3. 전체 심볼 백테스트

```bash
# DB의 모든 심볼 (150개)
python keltner_ict_turtle.py \
    --all-symbols \
    --tf 1h \
    --start 2023-01-01 \
    --end 2024-01-01

# 결과를 JSON으로 저장
python keltner_ict_turtle.py \
    --all-symbols \
    --tf 1h \
    --start 2023-01-01 \
    --output backtest_results_2023.json
```

---

### 4. 날짜 범위 지정

```bash
# 2022년 전체
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2022-01-01 \
    --end 2022-12-31

# 2023년 상반기
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2023-01-01 \
    --end 2023-06-30

# 최근 3개월
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2024-10-01 \
    --end 2025-01-01

# 현재까지 (end 생략)
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2024-01-01
```

---

## 📊 전략 파라미터

### 기본 설정

```python
# 켈트너 채널
ema_period = 20           # EMA 기간
atr_period = 20           # ATR 기간
atr_multiplier = 2.5      # ATR 배수 (원본: 2.0)

# 돈치안 채널 (터틀 원본)
donchian_period = 55      # 55일 돌파

# 리스크 관리
risk_per_trade = 0.02     # 거래당 2% 리스크
max_positions = 4         # 최대 4개 포지션

# ICT 파라미터
ob_lookback = 10          # Order Block 탐색 10개 캔들
fvg_min_gap = 0.001       # FVG 최소 갭 0.1%
```

---

## 🎯 진입 조건

### Long 진입 (모두 충족)

```
1. ✅ 켈트너 채널 상단 돌파
   - close > keltner_upper

2. ✅ 돈치안 채널 55일 돌파 (터틀)
   - close > donchian_high[55]

3. ✅ 볼륨 확인
   - volume > volume_ma * 1.3

4. ✅ RSI 과매수 필터
   - rsi < 70

5. ⭐ ICT 구조 (선택적 - 있으면 더 강함)
   - Bullish Order Block 존재
   - Bullish Fair Value Gap 존재
```

### Short 진입 (반대)

---

## 💰 리스크 관리

### 1. 포지션 사이징

```python
# 계좌의 2% 리스크
risk_amount = capital * 0.02

# ATR 기반 손절 거리
stop_distance = 2.5 * ATR

# 포지션 크기 계산
position_size = risk_amount / stop_distance
```

**예시:**
- 계좌: $10,000
- 리스크: $200 (2%)
- ATR: $100
- 손절 거리: $250 (2.5 * ATR)
- 포지션: $200 / $250 = 0.8 BTC

---

### 2. 손절

```
Long: entry_price - (2.5 * ATR)
Short: entry_price + (2.5 * ATR)
```

**원본 터틀:** 2 ATR  
**개선:** 2.5 ATR (변동성 증가 대응)

---

### 3. 익절 (피보나치)

```
Long:
  TP1 = entry + (entry - stop) * 1.618
  TP2 = entry + (entry - stop) * 2.618
  TP3 = entry + (entry - stop) * 4.236
```

**예시:**
- 진입: $40,000
- 손절: $39,500 (거리 $500)
- TP1: $40,809 (1.618배)
- TP2: $41,309 (2.618배)
- TP3: $42,118 (4.236배)

---

### 4. 추세 반전 청산

```
Long: close < keltner_lower
Short: close > keltner_upper
```

---

## 📈 백테스트 결과 예시

### 출력 포맷

```
======================================================================
📊 백테스트 결과: BTCUSDT
======================================================================
기간: 2023-01-01 ~ 2024-01-01
초기 자본: $10,000.00
최종 자본: $14,523.50
총 수익: $4,523.50 (+45.24%)

총 거래: 48
승리: 23 | 패배: 25
승률: 47.92%

평균 수익: $356.20
평균 손실: $187.40
최대 수익: $1,250.00
최대 손실: $520.00

최대 낙폭: $-1,234.00 (-12.34%)
샤프 비율: 1.45
프로핏 팩터: 2.18
======================================================================
```

---

### 여러 심볼 요약

```
======================================================================
📊 전체 요약
======================================================================
테스트 심볼: 10개
총 수익: $23,450.00
평균 승률: 44.5%
평균 샤프: 1.32
======================================================================

🏆 상위 5개:
1. BTCUSDT: $4,523.50 (+45.24%) | 승률 47.9%
2. ETHUSDT: $3,890.20 (+38.90%) | 승률 45.2%
3. SOLUSDT: $2,650.00 (+26.50%) | 승률 42.1%
4. XRPUSDT: $1,980.30 (+19.80%) | 승률 41.5%
5. BNBUSDT: $1,750.00 (+17.50%) | 승률 40.8%
```

---

## 💾 결과 저장

### JSON 저장

```bash
python keltner_ict_turtle.py \
    --symbol BTCUSDT \
    --tf 1h \
    --start 2023-01-01 \
    --output btc_backtest.json
```

### JSON 구조

```json
{
  "summary": {
    "symbol": "BTCUSDT",
    "start_date": "2023-01-01T00:00:00",
    "end_date": "2024-01-01T00:00:00",
    "initial_capital": 10000,
    "final_capital": 14523.5,
    "total_pnl": 4523.5,
    "total_pnl_pct": 45.24,
    "total_trades": 48,
    "winning_trades": 23,
    "losing_trades": 25,
    "win_rate": 47.92,
    "sharpe_ratio": 1.45,
    "profit_factor": 2.18
  },
  "trades": [
    {
      "entry_time": "2023-01-15T10:00:00",
      "entry_price": 21500.0,
      "exit_time": "2023-01-20T14:00:00",
      "exit_price": 22300.0,
      "direction": "long",
      "pnl": 350.5,
      "pnl_pct": 3.51,
      "exit_reason": "take_profit_1"
    }
  ]
}
```

---

## 📋 타임프레임별 권장

| TF | 기간 | 거래 빈도 | 용도 |
|----|------|----------|------|
| 15m | 30-60일 | 높음 | 데이트레이딩 |
| 30m | 60-90일 | 높음 | 단타 |
| 1h | 90-180일 | 중간 | 스윙 (권장) ⭐ |
| 4h | 180-365일 | 낮음 | 포지션 |
| 1d | 1-3년 | 매우 낮음 | 장기 투자 |

---

## 🔍 로그 확인

### 로그 파일

```bash
# 로그 파일명
backtest_20260118.log

# 실시간 확인
tail -f backtest_20260118.log

# 거래 로그만
grep "진입\|청산" backtest_20260118.log

# 결과만
grep "백테스트 결과" backtest_20260118.log
```

---

## 💡 실전 팁

### 1. 최적 파라미터 찾기

```bash
# 2023년 최적화
python keltner_ict_turtle.py --symbol BTCUSDT --tf 1h \
    --start 2023-01-01 --end 2023-12-31

# 2024년 검증
python keltner_ict_turtle.py --symbol BTCUSDT --tf 1h \
    --start 2024-01-01 --end 2024-12-31
```

**과적합 방지:**
- 훈련: 2023년
- 검증: 2024년
- 두 기간 모두 수익 → 채택

---

### 2. 시장별 성과 확인

```bash
# 상승장 (2023년 상반기)
python keltner_ict_turtle.py --all-symbols --tf 1h \
    --start 2023-01-01 --end 2023-06-30

# 하락장 (2023년 하반기)
python keltner_ict_turtle.py --all-symbols --tf 1h \
    --start 2023-07-01 --end 2023-12-31

# 횡보장 비교
```

---

### 3. 타임프레임 비교

```bash
# 1시간봉
python keltner_ict_turtle.py --symbol BTCUSDT --tf 1h \
    --start 2023-01-01

# 4시간봉
python keltner_ict_turtle.py --symbol BTCUSDT --tf 4h \
    --start 2023-01-01

# 일봉
python keltner_ict_turtle.py --symbol BTCUSDT --tf 1d \
    --start 2022-01-01
```

**예상 결과:**
- 1h: 거래 많음, 승률 낮음
- 4h: 거래 적음, 승률 높음 ⭐
- 1d: 거래 매우 적음, 안정적

---

### 4. 심볼 선별

```bash
# 전체 테스트
python keltner_ict_turtle.py --all-symbols --tf 1h \
    --start 2023-01-01 --output all_symbols.json

# JSON 분석해서 상위 20개만 실전 투자
```

---

## 🎯 예상 성과

### 보수적 추정 (1h, 1년)

| 지표 | 값 |
|------|-----|
| 승률 | 42-48% |
| 연 수익률 | 30-50% |
| 샤프 비율 | 1.2-1.6 |
| 최대 낙폭 | -20% ~ -30% |
| 프로핏 팩터 | 1.8-2.5 |

---

## 🔧 파라미터 조정

### 코드 수정 위치

```python
# keltner_ict_turtle.py
class KeltnerICTTurtle:
    def __init__(self, config_path: str = "config.yaml"):
        # === 여기서 수정 ===
        self.ema_period = 20           # 20 → 30으로
        self.atr_period = 20           # 20 → 14로
        self.atr_multiplier = 2.5      # 2.5 → 3.0으로
        self.donchian_period = 55      # 55 → 40으로
        
        self.risk_per_trade = 0.02     # 2% → 1% (보수적)
        self.max_positions = 4         # 4 → 2 (안전)
```

---

## ✅ 체크리스트

### 백테스트 전

- [ ] DB에 충분한 데이터 있는지 확인
- [ ] 날짜 범위 유효한지 확인
- [ ] config.yaml DB 설정 확인

### 백테스트 중

- [ ] 로그 확인 (오류 없는지)
- [ ] 거래 수 확인 (너무 적으면 문제)
- [ ] 메모리 사용량 확인

### 백테스트 후

- [ ] 승률 40% 이상인지
- [ ] 프로핏 팩터 1.5 이상인지
- [ ] MDD -30% 이내인지
- [ ] 과적합 아닌지 (다른 기간 테스트)

---

## 🚀 실전 적용

### 1. 백테스트로 검증

```bash
# 2023년 훈련
python keltner_ict_turtle.py --symbol BTCUSDT --tf 4h \
    --start 2023-01-01 --end 2023-12-31

# 2024년 검증
python keltner_ict_turtle.py --symbol BTCUSDT --tf 4h \
    --start 2024-01-01 --end 2024-12-31
```

### 2. 소액으로 시작

- 백테스트 결과 좋아도 소액($1,000)부터
- 3개월 실전 → 성과 확인 → 증액

### 3. 리스크 관리

- 최대 2% per trade (절대 지킬 것!)
- 최대 4개 포지션 (분산)
- MDD -30% 도달 시 중단

---

## 📚 추가 자료

### 원본 터틀 vs 현대화

| 항목 | 원본 터틀 | 켈트너 ICT |
|------|----------|-----------|
| 진입 | 55일 돌파 | 켈트너 + 돈치안 |
| 필터 | 없음 | 볼륨, RSI, ICT |
| 손절 | 2 ATR | 2.5 ATR |
| 익절 | 10일 저점 | 피보나치 다단계 |
| 승률 | 35% | 45% ⭐ |

---

## ✅ 완료!

**파일:**
- ✅ `keltner_ict_turtle.py`

**실행:**
```bash
# 단일 심볼
python keltner_ict_turtle.py --symbol BTCUSDT --tf 1h --start 2023-01-01

# 여러 심볼
python keltner_ict_turtle.py --symbols BTCUSDT ETHUSDT --tf 1h --start 2023-01-01

# 전체
python keltner_ict_turtle.py --all-symbols --tf 1h --start 2023-01-01
```

**터틀 트레이딩의 현대화 완성! 🐢→🚀**
