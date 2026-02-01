# 전략별 진입 규칙 가이드

## 📋 개요

현재 `RealtimeTradingEngine`에서 사용 가능한 전략들의 진입 규칙을 정리한 문서입니다.

---

## 1. EMA Cross Strategy (ema_cross)

### 진입 조건

#### 매수 신호 (BUY)
1. **필수 조건**:
   - ✅ **골든 크로스 발생**: EMA20이 EMA50을 상향 돌파
   - ✅ **EMA 정배열**: EMA20 > EMA50 > EMA200 (필수, 아니면 신호 무시)

2. **점수 시스템** (최소 점수: 0.7 기본값):
   - **EMA 정배열** (+0.4점): 필수 조건
   - **RSI 조건** (+0.1~0.15점):
     - RSI 30~70 범위: +0.1점
     - RSI ≤ 30 (과매도 반등): +0.15점
   - **VWAP 위치** (+0.1점): 가격 > VWAP
   - **거래량 증가** (+0.1점): 현재 거래량 > 평균 거래량 × 1.2
   - **EMA 간격** (+0.1점): (EMA20 - EMA50) / EMA50 × 100 > 0.5%

3. **SL/TP 계산**:
   - **Stop Loss**: Entry - (ATR × atr_multiplier) [기본 1.5]
   - **Take Profit 1**: Entry + (ATR × atr_multiplier × 1.5)
   - **Take Profit 2**: Entry + (ATR × atr_multiplier × 2.5)

#### 매도 신호 (SELL)
1. **필수 조건**:
   - ✅ **데드 크로스 발생**: EMA20이 EMA50을 하향 돌파
   - ✅ **EMA 역배열**: EMA20 < EMA50 < EMA200 (필수, 아니면 신호 무시)

2. **점수 시스템** (최소 점수: 0.7 기본값):
   - **EMA 역배열** (+0.4점): 필수 조건
   - **RSI 조건** (+0.1~0.15점):
     - RSI 30~70 범위: +0.1점
     - RSI ≥ 70 (과매수 하락): +0.15점
   - **VWAP 위치** (+0.1점): 가격 < VWAP
   - **거래량 증가** (+0.1점): 현재 거래량 > 평균 거래량 × 1.2
   - **EMA 간격** (+0.1점): (EMA50 - EMA20) / EMA50 × 100 > 0.5%

3. **SL/TP 계산**:
   - **Stop Loss**: Entry + (ATR × atr_multiplier) [기본 1.5]
   - **Take Profit 1**: Entry - (ATR × atr_multiplier × 1.5)
   - **Take Profit 2**: Entry - (ATR × atr_multiplier × 2.5)

### 설정 파라미터
- `score_threshold`: 최소 점수 (기본: 0.7)
- `atr_multiplier`: ATR 배수 (기본: 1.5)
- `use_rsi_filter`: RSI 필터 사용 여부 (기본: True)
- `use_vwap_filter`: VWAP 필터 사용 여부 (기본: True)

---

## 2. ICT Strategy (ict)

### 진입 조건

#### 매수 신호 (BUY)
1. **Order Block (OB) 또는 Fair Value Gap (FVG) 탐지**
2. **현재가가 OB/FVG 영역에 위치**
3. **상위 타임프레임 레짐 확인** (선택적)
4. **ATR 기반 SL/TP 계산**

#### 매도 신호 (SELL)
1. **Bearish Order Block 또는 FVG 탐지**
2. **현재가가 OB/FVG 영역에 위치**
3. **상위 타임프레임 레짐 확인** (선택적)
4. **ATR 기반 SL/TP 계산**

---

## 3. RSI Strategy (rsi)

### 진입 조건

#### 매수 신호 (BUY)
- **과매도 탈출**: 이전 RSI ≤ 30 → 현재 RSI > 30
- RSI가 과매도 구간에서 벗어날 때 진입

#### 매도 신호 (SELL)
- **과매수 탈출**: 이전 RSI ≥ 70 → 현재 RSI < 70
- RSI가 과매수 구간에서 벗어날 때 진입

---

## 4. Bollinger Strategy (bollinger)

### 진입 조건

#### 매수 신호 (BUY)
- **밴드 터치 후 반등**: 가격이 하단 밴드에 닿은 후 상승
- **볼린저 밴드 수축 후 확장**: 변동성 증가 시 진입

#### 매도 신호 (SELL)
- **밴드 터치 후 하락**: 가격이 상단 밴드에 닿은 후 하락
- **볼린저 밴드 수축 후 확장**: 변동성 증가 시 진입

---

## 5. Pattern RAG Strategy (pattern_rag)

### 진입 조건
- **과거 유사 패턴 학습 기반**
- **AI 신호 생성** (OLLAMA LLM 사용)
- **패턴 유사도 기반 신뢰도 계산**

---

## 6. BB Adaptive RSI Strategy (bb_adaptive_rsi)

### 진입 조건
- **볼린저 밴드 3σ + Adaptive RSI**
- **Kaufman ER 기반 RSI 기간 조정**
- **변동성에 따른 적응형 진입**

---

## 7. Keltner ICT Turtle Strategy (keltner_ict_turtle)

### 진입 조건
- **켈트너 채널 + ICT + 터틀 트레이딩 통합**
- **채널 돌파 기반 진입**
- **터틀 트레이딩 포지션 사이징**

---

## 🔍 현재 사용 중인 전략 확인 방법

### 1. 서버 로그 확인
서버 시작 시 로그에서 확인:
```
🔄 RealtimeTradingEngine 초기화: ETHUSDT ema_cross 5m
```

### 2. API로 확인
```bash
GET /api/realtime/status
```

응답에서 `strategy` 필드 확인:
```json
{
  "strategy": "ema_cross",
  "symbol": "ETHUSDT",
  "timeframe": "5m"
}
```

### 3. 코드에서 확인
`realtime_trading_engine.py`의 `self.strategy_name` 변수

---

## 📊 진입 규칙 요약

### 공통 사항
- 모든 전략은 **캔들이 완성될 때마다** (`_handle_closed_candle`) 실행
- **이미 오픈 포지션이 있으면** 새로운 진입 신호 무시
- **최소 50개 캔들** 필요 (일부 전략은 100개)
- **첫 번째 신호만 사용** (`signals[0]`)

### 리스크 관리
- **기본 리스크**: 자본의 2%
- **수량 계산**: `quantity = risk_amount / stop_distance`
- **SL/TP**: 전략별 ATR 기반 계산

---

## ⚙️ 전략 변경 방법

### 1. 서버 시작 시 전략 지정
```json
POST /api/realtime/start
{
  "symbol": "ETHUSDT",
  "strategy": "ema_cross",  // 여기서 전략 변경
  "timeframe": "5m"
}
```

### 2. 사용 가능한 전략 목록
- `ema_cross`: EMA Cross + HTF 정렬
- `ict`: ICT Order Block + FVG
- `rsi`: RSI 과매수/과매도
- `bollinger`: 볼린저 밴드 Breakout/Reversal
- `keltner_ict_turtle`: 켈트너 + ICT + 터틀
- `pattern_rag`: 패턴 학습 + AI
- `bb_adaptive_rsi`: BB 3σ + Adaptive RSI

---

## 📝 참고

- 각 전략의 상세 진입 규칙은 해당 전략 파일에서 확인 가능
- `config.yaml`의 `strategies` 섹션에서 전략별 설정 조정 가능
- 진입 규칙은 전략의 `analyze()` 메서드에 구현되어 있음
