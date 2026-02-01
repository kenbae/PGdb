# 자동매매 시스템 프로젝트 구조

## 디렉토리 구조

```
trading_system/
├── config/
│   ├── config.yaml                 # 전역 설정
│   ├── strategies/                 # 전략별 설정
│   │   ├── ict_ai.yaml
│   │   ├── turtle.yaml
│   │   └── ema_cross.yaml
│   └── exchanges/                  # 거래소 설정
│       ├── binance_testnet.yaml
│       └── binance_live.yaml
│
├── core/                           # 핵심 엔진
│   ├── __init__.py
│   ├── engine.py                   # 메인 트레이딩 엔진
│   ├── data_feed.py                # 실시간 데이터 피드
│   ├── order_manager.py            # 주문 관리
│   ├── position_manager.py         # 포지션 관리
│   ├── risk_manager.py             # 리스크 관리
│   └── event_bus.py                # 이벤트 버스
│
├── strategies/                     # 전략 플러그인
│   ├── __init__.py
│   ├── base_strategy.py            # 전략 베이스 클래스
│   ├── ict_ai_strategy.py          # ICT + AI 전략 ⭐
│   ├── turtle_strategy.py          # 터틀 트레이딩
│   ├── ema_cross_strategy.py       # EMA 크로스오버
│   └── custom/                     # 커스텀 전략
│       └── my_strategy.py
│
├── exchanges/                      # 거래소 연동
│   ├── __init__.py
│   ├── base_exchange.py            # 거래소 베이스 클래스
│   ├── binance.py                  # Binance 실전
│   ├── binance_testnet.py          # Binance 테스트넷
│   └── paper_exchange.py           # 페이퍼 트레이딩
│
├── indicators/                     # 지표 라이브러리
│   ├── __init__.py
│   ├── trend.py                    # EMA, MA, MACD
│   ├── volatility.py               # ATR, Bollinger
│   ├── ict.py                      # ICT 지표 ⭐
│   │   ├── order_blocks.py
│   │   ├── fair_value_gaps.py
│   │   ├── liquidity_levels.py
│   │   └── market_structure.py
│   └── custom.py
│
├── ai/                             # AI 분석 ⭐
│   ├── __init__.py
│   ├── ollama_client.py            # OLLAMA API
│   ├── prompt_templates.py         # 프롬프트 템플릿
│   ├── signal_analyzer.py          # AI 신호 분석
│   └── confidence_scorer.py        # 신뢰도 점수
│
├── database/                       # 데이터베이스
│   ├── __init__.py
│   ├── models.py                   # SQLAlchemy 모델
│   ├── repositories/               # 데이터 저장소
│   │   ├── candle_repo.py
│   │   ├── signal_repo.py
│   │   ├── trade_repo.py
│   │   └── performance_repo.py
│   └── migrations/                 # DB 마이그레이션
│
├── monitoring/                     # 모니터링
│   ├── __init__.py
│   ├── dashboard.py                # 실시간 대시보드
│   ├── logger.py                   # 로깅
│   ├── alerts.py                   # 알림 (텔레그램)
│   └── metrics.py                  # 성과 추적
│
├── backtesting/                    # 백테스트
│   ├── __init__.py
│   ├── backtest_engine.py          # 백테스트 엔진
│   ├── report_generator.py         # 리포트 생성
│   └── optimizer.py                # 파라미터 최적화
│
├── utils/                          # 유틸리티
│   ├── __init__.py
│   ├── validators.py               # 유효성 검증
│   ├── formatters.py               # 데이터 포맷
│   └── helpers.py                  # 헬퍼 함수
│
├── tests/                          # 테스트
│   ├── unit/
│   ├── integration/
│   └── strategies/
│
├── scripts/                        # 스크립트
│   ├── setup_testnet.py            # 테스트넷 설정
│   ├── deploy.py                   # 배포
│   └── emergency_stop.py           # 긴급 정지
│
├── logs/                           # 로그 파일
├── data/                           # 데이터 캐시
├── .env                            # 환경 변수 (API 키)
├── requirements.txt                # 패키지 의존성
└── main.py                         # 메인 엔트리포인트
```

---

## 핵심 컴포넌트 설명

### 1. Core Engine
- **engine.py**: 메인 트레이딩 루프
- **event_bus.py**: 전략 간 통신
- **order_manager.py**: 주문 실행
- **position_manager.py**: 포지션 추적
- **risk_manager.py**: 리스크 제어

### 2. Strategy Plugin System
- **base_strategy.py**: 모든 전략의 기본 클래스
- **플러그인 방식**: 새 전략을 쉽게 추가
- **설정 분리**: YAML로 전략 파라미터 관리

### 3. ICT + AI Strategy ⭐
- **ict.py**: Order Blocks, FVG, Liquidity
- **ai/**: OLLAMA 기반 신호 분석
- **confidence_scorer.py**: AI 신뢰도 점수

### 4. Exchange Layer
- **base_exchange.py**: 거래소 추상화
- **binance_testnet.py**: 안전한 테스트
- **paper_exchange.py**: 로컬 시뮬레이션

### 5. Monitoring
- **dashboard.py**: 실시간 모니터링
- **alerts.py**: 텔레그램 알림
- **metrics.py**: 성과 추적

---

## 전략 추가 방법

### 새 전략 만들기 (3단계)

**1단계: 전략 클래스 생성**
```python
# strategies/my_strategy.py
from strategies.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    def generate_signal(self, df):
        # 신호 생성 로직
        return signal
```

**2단계: 설정 파일 생성**
```yaml
# config/strategies/my_strategy.yaml
name: "My Custom Strategy"
enabled: true
parameters:
  period: 20
  threshold: 0.5
```

**3단계: 등록**
```python
# main.py
engine.register_strategy(MyStrategy)
```

---

## 실행 모드

### 1. 테스트넷 모드 (안전)
```bash
python main.py --mode testnet --strategy ict_ai
```

### 2. 페이퍼 트레이딩 (시뮬레이션)
```bash
python main.py --mode paper --strategy ict_ai
```

### 3. 실전 모드 (소액)
```bash
python main.py --mode live --strategy ict_ai --capital 100
```

---

## 특징

✅ **플러그인 아키텍처**: 전략을 쉽게 추가/제거
✅ **멀티 전략**: 여러 전략 동시 실행
✅ **테스트넷 우선**: 안전한 개발
✅ **AI 통합**: OLLAMA 기반 분석
✅ **확장 가능**: 새 거래소/전략 추가 용이
✅ **모니터링**: 실시간 대시보드
✅ **백테스트**: 전략 검증

---

## 다음 단계

1. 핵심 엔진 개발
2. ICT + AI 전략 구현
3. 테스트넷 테스트
4. 페이퍼 트레이딩
5. 소액 실전
