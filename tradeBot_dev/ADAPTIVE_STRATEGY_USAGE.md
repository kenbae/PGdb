# 적응형 전략 시스템 사용 가이드

## 🚀 빠른 시작

### 1. 데이터베이스 테이블 생성

```bash
cd PGdb/tradeBot_dev/database
python setup_strategy_performance.py
```

### 2. 기본 사용법

```python
from services.adaptive_strategy_manager import AdaptiveStrategyManager
from services.strategy_performance_tracker import StrategyPerformanceTracker

# DB 엔진 생성
from PGdb.config import get_db_config
from sqlalchemy import create_engine
db_config = get_db_config()
db_url = f"postgresql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}"
engine = create_engine(db_url)

# 적응형 전략 매니저 생성
adaptive_manager = AdaptiveStrategyManager(
    db_engine=engine,
    symbol='ETHUSDT',
    timeframe='5m',
    auto_switch=False,  # 자동 전환 비활성화 (제안만)
    suggest_switches=True,  # 제안 활성화
    min_confidence=0.7,  # 최소 신뢰도 70%
    min_switch_interval_hours=24  # 최소 전환 간격 24시간
)

# 현재 전략 평가 및 제안 받기
suggestion = adaptive_manager.evaluate_and_suggest('ema_cross')
if suggestion:
    print(f"💡 전환 제안: {suggestion['current_strategy']} → {suggestion['suggested_strategy']}")
    print(f"   예상 개선: {suggestion['improvement_pct']}%")
    print(f"   신뢰도: {suggestion['confidence']*100:.1f}%")
    
    # 수동으로 전환 승인
    if input("전환하시겠습니까? (y/n): ") == 'y':
        adaptive_manager.switch_strategy(
            from_strategy=suggestion['current_strategy'],
            to_strategy=suggestion['suggested_strategy'],
            switch_reason=f"성과 개선: {suggestion['improvement_pct']}%",
            switch_type='manual'
        )
```

### 3. 자동 전환 모드

```python
# 자동 전환 활성화
adaptive_manager = AdaptiveStrategyManager(
    db_engine=engine,
    symbol='ETHUSDT',
    timeframe='5m',
    auto_switch=True,  # 자동 전환 활성화
    min_confidence=0.8,  # 높은 신뢰도 필요
    min_switch_interval_hours=48  # 최소 48시간 간격
)

# 주기적으로 평가 (예: 1시간마다)
suggestion = adaptive_manager.evaluate_and_suggest('ema_cross')
if suggestion and suggestion['should_switch']:
    # 자동으로 전환
    adaptive_manager.switch_strategy(
        from_strategy=suggestion['current_strategy'],
        to_strategy=suggestion['suggested_strategy'],
        switch_reason=f"자동 전환: {suggestion['improvement_pct']}% 개선",
        switch_type='auto'
    )
    # 실제 전략도 변경해야 함 (RealtimeTradingEngine에 통합 필요)
```

---

## 📊 성과 추적

### 1. 성과 계산 및 저장

```python
from services.strategy_performance_tracker import StrategyPerformanceTracker

tracker = StrategyPerformanceTracker(engine)

# 전략 성과 계산
performance = tracker.calculate_performance(
    symbol='ETHUSDT',
    strategy_name='ema_cross',
    timeframe='5m',
    period_type='medium',  # 최근 30개 거래 기준
    min_trades=5
)

if performance:
    print(f"승률: {performance['win_rate']}%")
    print(f"평균 수익률: {performance['avg_return']}%")
    print(f"종합 점수: {performance['composite_score']}")
    
    # DB에 저장
    tracker.save_performance(performance)
```

### 2. 최고 전략 조회

```python
# 심볼별 최고 성과 전략
best_strategy = tracker.get_best_strategy(
    symbol='ETHUSDT',
    timeframe='5m',
    period_type='medium',
    min_score=50.0  # 최소 점수
)

if best_strategy:
    print(f"최고 전략: {best_strategy['strategy_name']}")
    print(f"종합 점수: {best_strategy['composite_score']}")
```

### 3. 모든 전략 성과 비교

```python
# 모든 전략 성과 조회 (점수 순)
all_performances = tracker.get_all_strategies_performance(
    symbol='ETHUSDT',
    timeframe='5m',
    period_type='medium'
)

for perf in all_performances:
    print(f"{perf['strategy_name']}: {perf['composite_score']:.2f}점 "
          f"(승률: {perf['win_rate']}%, 수익률: {perf['avg_return']}%)")
```

---

## 🔄 RealtimeTradingEngine 통합

### 1. 포지션 저장 시 전략 정보 포함

`realtime_trading_engine.py`에서 포지션 저장 시 `metadata`에 전략 이름을 포함:

```python
# 포지션 저장 시
position_data = {
    'position_id': position.id,
    'symbol': self.symbol,
    'side': position.side.value,
    'entry_price': float(position.entry_price),
    'exit_price': float(position.exit_price) if position.exit_price else None,
    'quantity': float(position.quantity),
    'pnl': float(position.pnl) if position.pnl else None,
    'pnl_percent': float(position.pnl_percent) if position.pnl_percent else None,
    'status': position.status.value,
    'open_time': position.open_time,
    'close_time': position.close_time,
    'metadata': {
        'strategy_name': self.strategy_name,  # 전략 이름 추가
        'timeframe': self.timeframe,
        'exit_reason': exit_reason
    }
}
```

### 2. 주기적 성과 평가

`RealtimeTradingEngine`에 주기적 평가 로직 추가:

```python
import asyncio
from services.adaptive_strategy_manager import AdaptiveStrategyManager

class RealtimeTradingEngine:
    def __init__(self, ...):
        # ... 기존 코드 ...
        
        # 적응형 전략 매니저 (선택적)
        self.adaptive_manager = None  # 필요시 초기화
        
    async def _periodic_strategy_evaluation(self):
        """주기적 전략 평가 (예: 1시간마다)"""
        if not self.adaptive_manager:
            return
            
        while self.is_running:
            await asyncio.sleep(3600)  # 1시간 대기
            
            try:
                # 현재 전략 평가
                suggestion = self.adaptive_manager.evaluate_and_suggest(
                    self.strategy_name
                )
                
                if suggestion:
                    if suggestion['should_switch']:
                        # 자동 전환
                        self._add_log(
                            f"🔄 전략 자동 전환: {suggestion['current_strategy']} → "
                            f"{suggestion['suggested_strategy']} "
                            f"(개선: {suggestion['improvement_pct']}%)",
                            "info"
                        )
                        # 실제 전략 변경 로직 필요
                    else:
                        # 제안만
                        self._add_log(
                            f"💡 전략 전환 제안: {suggestion['current_strategy']} → "
                            f"{suggestion['suggested_strategy']} "
                            f"(개선: {suggestion['improvement_pct']}%)",
                            "info"
                        )
            except Exception as e:
                logger.error(f"전략 평가 실패: {e}")
```

### 3. 거래 완료 시 성과 업데이트

포지션이 종료될 때마다 성과를 업데이트:

```python
async def _close_position(self, position, exit_reason):
    # ... 기존 청산 로직 ...
    
    # 포지션 저장
    if self.positions_repo:
        # ... 포지션 저장 ...
    
    # 성과 추적 업데이트 (비동기로 실행)
    if self.adaptive_manager:
        asyncio.create_task(self._update_strategy_performance())
        
async def _update_strategy_performance(self):
    """전략 성과 업데이트"""
    try:
        tracker = self.adaptive_manager.performance_tracker
        
        # 성과 계산
        performance = tracker.calculate_performance(
            symbol=self.symbol,
            strategy_name=self.strategy_name,
            timeframe=self.timeframe,
            period_type='medium'
        )
        
        if performance:
            # DB 저장
            tracker.save_performance(performance)
            self._add_log(
                f"📊 전략 성과 업데이트: {self.strategy_name} "
                f"(점수: {performance['composite_score']:.2f}, "
                f"승률: {performance['win_rate']}%)",
                "info"
            )
    except Exception as e:
        logger.error(f"성과 업데이트 실패: {e}")
```

---

## 📈 성과 리포트

```python
# 성과 리포트 생성
report = adaptive_manager.get_performance_report()

print(f"심볼: {report['symbol']}")
print(f"최고 전략: {report['best_strategy']}")
print(f"최고 점수: {report['best_score']}")
print("\n전략별 성과:")
for strategy in report['strategies']:
    print(f"  {strategy['strategy_name']}: {strategy['composite_score']:.2f}점")
```

---

## ⚙️ 설정 옵션

### AdaptiveStrategyManager 파라미터

- `auto_switch`: 자동 전환 활성화 여부 (기본: False)
- `suggest_switches`: 전환 제안 활성화 여부 (기본: True)
- `min_confidence`: 최소 신뢰도 0-1 (기본: 0.7)
- `min_switch_interval_hours`: 최소 전환 간격 시간 (기본: 24)
- `min_trades_for_evaluation`: 평가를 위한 최소 거래 수 (기본: 10)

### 전환 조건

- **자동 전환**: 성과 차이 20% 이상 + 신뢰도 충분
- **제안**: 성과 차이 10% 이상
- **전환 방지**: 최소 간격 미달 또는 거래 수 부족

---

## 🎯 다음 단계

1. **UI 대시보드 추가**: 웹 인터페이스에서 전략 성과 및 제안 확인
2. **알림 시스템**: 전략 전환 제안 시 알림 (이메일, 슬랙 등)
3. **백테스트 통합**: 전환 전 백테스트로 검증
4. **머신러닝 예측**: 과거 패턴으로 미래 성과 예측
