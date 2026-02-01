# Pattern RAG 시스템 가이드

## 개요

Pattern RAG(Retrieval-Augmented Generation)는 과거 캔들 패턴을 학습하고, 현재 패턴과 유사한 과거 사례를 찾아 OLLAMA AI로 매매 신호를 생성하는 시스템입니다.

### 작동 원리

```
1. 학습 단계 (Learn)
   과거 OHLCV 데이터
        ↓
   패턴 특징 추출 (13차원 벡터)
        ↓
   SQLite DB 저장 (미래 수익률 포함)

2. 신호 생성 단계 (Generate)
   현재 캔들 패턴 추출
        ↓
   유사 패턴 검색 (코사인 유사도)
        ↓
   통계 분석 (상승확률, 평균수익률)
        ↓
   OLLAMA AI 분석
        ↓
   최종 신호 생성
```

---

## 패턴 특징 벡터 (13차원)

| 특징 | 설명 | 범위 |
|------|------|------|
| price_change_pct | 기간 내 가격 변화율 | -1 ~ 1 |
| high_low_range | 고가-저가 범위 (ATR 대비) | 0 ~ 5 |
| close_position | 캔들 내 종가 위치 | 0 ~ 1 |
| body_ratio | 몸통 비율 | 0 ~ 1 |
| ema_trend | EMA 추세 강도 | -1 ~ 1 |
| ema_alignment | EMA 정배열(1)/역배열(-1)/혼합(0) | -1, 0, 1 |
| price_vs_ema | 가격 vs EMA 위치 | -1 ~ 1 |
| rsi_value | RSI (정규화) | 0 ~ 1 |
| rsi_divergence | RSI 다이버전스 | -1 ~ 1 |
| volume_ratio | 평균 대비 볼륨 비율 | 0 ~ 5 |
| volume_trend | 볼륨 추세 | -1 ~ 1 |
| atr_ratio | ATR / 가격 비율 | 0 ~ 1 |
| bb_position | 볼린저 밴드 내 위치 | 0 ~ 1 |

---

## 사용 방법

### 1. 전략으로 자동 사용 (권장)

`config.yaml`에서 설정하면 다른 전략들과 함께 자동 실행됩니다.

```yaml
strategies:
  pattern_rag:
    enabled: true
    use_ai: false               # 전략 자체가 AI 사용
    timeframe: "1h"

    # OLLAMA 설정
    ollama_url: "http://localhost:11434"
    ollama_model: "qwen2.5:7b-instruct"

    # RAG 설정
    top_k: 10                   # 유사 패턴 검색 개수
    min_confidence: 0.6         # 최소 신뢰도 (0-1)
    min_patterns: 5             # 최소 유사 패턴 수
    auto_learn: true            # 자동 학습 여부
    learn_on_no_patterns: true  # 패턴 없을 때 자동 학습
    atr_multiplier: 2.0         # SL/TP 계산용
```

#### 설정 옵션 설명

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `top_k` | 10 | 유사 패턴 검색 시 상위 K개 반환 |
| `min_confidence` | 0.6 | 이 값 이상이어야 신호 생성 |
| `min_patterns` | 5 | 최소 유사 패턴 수 (부족하면 신호 미생성) |
| `auto_learn` | true | 처음 분석하는 심볼 자동 학습 |
| `learn_on_no_patterns` | true | 유사 패턴 부족 시 자동 학습 시도 |

### 2. 백테스터에서 사용

백테스터 웹 UI에서 `pattern_rag` 전략을 선택하면 됩니다.

```
http://localhost:5000  (백테스터)
→ 전략 선택에서 "pattern_rag" 체크
→ 심볼/타임프레임 선택
→ 백테스트 시작
```

### 3. Python 코드에서 직접 사용

```python
from tradeBot.ai.pattern_rag import PatternRAGSignalGenerator
import pandas as pd

# RAG 생성기 초기화
rag = PatternRAGSignalGenerator(
    ollama_host='http://localhost:11434',
    ollama_model='qwen2.5:7b-instruct',
    top_k=10
)

# 과거 데이터 로드 (최소 200개 캔들 권장)
df = pd.read_csv('btcusdt_1h.csv')  # OHLCV 데이터

# 1. 학습 (한 번만 실행)
saved = rag.learn_from_data(df, 'BTCUSDT', '1h')
print(f"{saved}개 패턴 학습됨")

# 2. 신호 생성
signal = rag.generate_signal(df, 'BTCUSDT', '1h')
print(signal)
```

#### 신호 출력 예시

```python
{
    'signal': 'buy',              # buy/sell/hold
    'confidence': 0.72,           # 신뢰도 (0-1)
    'entry_price': 95000.0,       # 진입가
    'stop_loss': 93100.0,         # 손절가
    'take_profit': 98800.0,       # 익절가
    'risk_level': 'medium',       # low/medium/high
    'reasoning': 'AI 판단 근거...',
    'similar_patterns': 10,       # 유사 패턴 수
    'pattern_stats': {
        'up_ratio': 0.8,          # 상승 확률 (80%)
        'avg_return': 2.5,        # 평균 수익률 (2.5%)
        'avg_similarity': 0.85    # 평균 유사도
    },
    'timestamp': '2025-01-21T10:30:00'
}
```

---

## 수동 학습

### 전략 클래스를 통한 학습

```python
from strategies import PatternRAGStrategy

# 전략 초기화
config = {
    'enabled': True,
    'ollama_url': 'http://localhost:11434',
    'ollama_model': 'qwen2.5:7b-instruct',
    'top_k': 10,
    'auto_learn': False  # 수동 학습 모드
}
strategy = PatternRAGStrategy('pattern_rag', config)

# 데이터 준비 (DataFrame with OHLCV)
df = get_historical_data('BTCUSDT', '1h', limit=1000)

# 학습
saved = strategy.learn_symbol('BTCUSDT', '1h', df)
print(f"저장된 패턴: {saved}개")

# 통계 확인
stats = strategy.get_pattern_stats('BTCUSDT', '1h')
print(stats)
# {'total_patterns': 450, 'up_patterns': 210, 'down_patterns': 195, 'neutral_patterns': 45}
```

### 직접 PatternDatabase 사용

```python
from tradeBot.ai.pattern_rag import PatternExtractor, PatternDatabase

# 추출기 및 DB 초기화
extractor = PatternExtractor(lookback=20, future_bars=10)
db = PatternDatabase()

# 패턴 추출
patterns = extractor.extract_all(df, 'BTCUSDT', '1h')

# DB에 저장
saved = db.save_patterns(patterns)

# 통계 조회
stats = db.get_stats('BTCUSDT', '1h')
```

---

## 학습 데이터 요구사항

### 최소 데이터량

| 타임프레임 | 최소 캔들 수 | 권장 캔들 수 | 기간 |
|------------|------------|------------|------|
| 15m | 500 | 2000+ | 약 5일 ~ 3주 |
| 1h | 300 | 1000+ | 약 2주 ~ 6주 |
| 4h | 200 | 500+ | 약 1개월 ~ 3개월 |
| 1d | 100 | 365+ | 약 3개월 ~ 1년 |

### 데이터 품질

- **결측치 없음**: OHLCV 모든 컬럼 필수
- **시간순 정렬**: 오래된 데이터 → 최신 데이터
- **충분한 변동성**: 횡보장만 있는 데이터는 학습 효과 낮음

---

## 패턴 데이터베이스

### 저장 위치

```
PGdb/tradeBot/data/patterns.db  (SQLite)
```

### 테이블 구조

```sql
CREATE TABLE patterns (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    vector TEXT NOT NULL,        -- JSON: 13차원 벡터
    features TEXT NOT NULL,      -- JSON: 전체 특징
    future_return REAL,          -- 미래 수익률
    future_direction INTEGER,    -- 1=상승, -1=하락, 0=횡보
    created_at TEXT
);
```

### DB 관리

```python
from tradeBot.ai.pattern_rag import PatternDatabase
import sqlite3

db = PatternDatabase()

# 전체 통계
stats = db.get_stats()
print(f"총 패턴: {stats['total_patterns']}")

# 심볼별 통계
btc_stats = db.get_stats('BTCUSDT', '1h')

# 직접 쿼리 (고급)
with sqlite3.connect(db.db_path) as conn:
    # 최근 패턴 조회
    cursor = conn.execute('''
        SELECT symbol, timeframe, COUNT(*) as cnt
        FROM patterns
        GROUP BY symbol, timeframe
        ORDER BY cnt DESC
    ''')
    for row in cursor:
        print(f"{row[0]} {row[1]}: {row[2]}개")
```

---

## 성능 최적화

### 1. 학습 데이터 양 조절

```python
# 너무 많은 데이터는 노이즈 증가
# 권장: 최근 3-6개월 데이터

df_recent = df.tail(1000)  # 최근 1000개 캔들만
rag.learn_from_data(df_recent, 'BTCUSDT', '1h')
```

### 2. top_k 조절

```yaml
# 유사 패턴이 적으면 신뢰도 낮음
# 너무 많으면 노이즈 증가
top_k: 15  # 권장: 5-15
```

### 3. min_confidence 조절

```yaml
# 높으면 신호 적지만 정확도 높음
# 낮으면 신호 많지만 노이즈 증가
min_confidence: 0.6  # 권장: 0.5-0.7
```

### 4. 심볼별 개별 학습

```python
# 각 심볼의 특성이 다르므로 개별 학습 권장
for symbol in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
    df = get_data(symbol, '1h', limit=1000)
    rag.learn_from_data(df, symbol, '1h')
```

---

## OLLAMA 설정

### 모델 선택

| 모델 | 속도 | 품질 | VRAM |
|------|------|------|------|
| qwen2.5:7b-instruct | 빠름 | 좋음 | 6GB |
| qwen2.5:14b-instruct | 보통 | 더 좋음 | 12GB |
| llama3.1:8b-instruct | 빠름 | 좋음 | 8GB |
| mistral:7b-instruct | 빠름 | 좋음 | 6GB |

### OLLAMA 설치 및 실행

```bash
# 설치 (Windows)
winget install Ollama.Ollama

# 모델 다운로드
ollama pull qwen2.5:7b-instruct

# 서버 실행 (자동 실행됨)
ollama serve
```

### 연결 확인

```python
import requests

response = requests.get('http://localhost:11434/api/tags')
if response.status_code == 200:
    print("OLLAMA 연결 성공")
    models = response.json().get('models', [])
    for m in models:
        print(f"  - {m['name']}")
```

---

## 트러블슈팅

### 1. "유사 패턴 없음" 오류

```
원인: 해당 심볼/타임프레임의 학습 데이터가 없음
해결: 수동 학습 실행

rag.learn_from_data(df, 'SYMBOL', 'TIMEFRAME')
```

### 2. OLLAMA 연결 실패

```
원인: OLLAMA 서버가 실행되지 않음
해결:
1. ollama serve 실행
2. http://localhost:11434 접속 확인
3. config.yaml의 ollama_url 확인
```

### 3. 신호가 생성되지 않음

```
원인:
1. confidence가 min_confidence보다 낮음
2. 유사 패턴이 min_patterns보다 적음

해결:
1. min_confidence 낮추기 (0.5)
2. min_patterns 낮추기 (3)
3. 더 많은 데이터로 학습
```

### 4. 학습이 느림

```
원인: 데이터가 너무 많음
해결: 최근 데이터만 학습

df_recent = df.tail(500)
rag.learn_from_data(df_recent, symbol, timeframe)
```

---

## API 참조

### PatternRAGSignalGenerator

```python
class PatternRAGSignalGenerator:
    def __init__(
        self,
        ollama_host: str = 'http://localhost:11434',
        ollama_model: str = 'qwen2.5:7b-instruct',
        db_path: str = None,
        top_k: int = 10
    )

    def learn_from_data(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str
    ) -> int:
        """과거 데이터 학습. 저장된 패턴 수 반환"""

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        current_price: float = None
    ) -> Dict:
        """신호 생성"""
```

### PatternRAGStrategy

```python
class PatternRAGStrategy(BaseStrategy):
    def analyze(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame
    ) -> List[TradeSignal]:
        """분석 및 신호 생성"""

    def learn_symbol(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame
    ) -> int:
        """수동 학습"""

    def get_pattern_stats(
        self,
        symbol: str = None,
        timeframe: str = None
    ) -> Dict:
        """패턴 통계 조회"""
```

---

## 주의사항

1. **과적합 위험**: 너무 적은 데이터나 특정 기간 데이터만 학습하면 과적합 발생
2. **시장 변화**: 시장 특성이 변하면 재학습 필요
3. **AI 의존성**: OLLAMA 서버가 필수 (오프라인 불가)
4. **리소스 사용**: GPU 권장 (CPU도 가능하나 느림)

---

## 버전 히스토리

- **v1.0** (2025-01-21): 초기 버전
  - 13차원 패턴 특징 벡터
  - 코사인 유사도 기반 검색
  - OLLAMA AI 연동
  - SQLite 패턴 저장
