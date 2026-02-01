# 단일 심볼/전략 실시간 Paper Trading 구현 계획

## 🎯 목표

단일 심볼과 단일 전략을 선택한 후, 실시간으로 시장을 모니터링하면서 해당 전략의 신호에 부합하면 자동으로 진입/청산하는 Paper Trading 시스템

---

## 💡 구현 방식

### 옵션 1: 기존 Paper Trading 확장 (추천 ⭐)

**장점**:
- 기존 코드 재사용
- 빠른 구현
- 기존 기능 유지 (다중 심볼 모드도 계속 사용 가능)

**구현**:
1. `PaperTradingEngine`에 필터 옵션 추가
2. `process_signal`에서 심볼/전략 필터링
3. UI에 심볼/전략 선택 추가

**단점**:
- 기존 코드 수정 필요

---

### 옵션 2: 별도 SingleSymbolPaperTradingEngine 생성

**장점**:
- 기존 코드 영향 없음
- 단순한 구조

**단점**:
- 코드 중복
- 유지보수 부담

---

## ✅ 선택: 옵션 1 (기존 확장)

기존 Paper Trading을 확장하여 필터링 기능을 추가하는 방식이 가장 효율적입니다.

---

## 📝 구현 계획

### 1. PaperTradingEngine 확장

**파일**: `services/paper_trading_engine.py`

**변경사항**:
```python
class PaperTradingEngine:
    def __init__(
        self,
        broker: PaperBroker,
        signals_repo,
        events_repo: EventsRepo,
        positions_repo: PositionsRepo,
        timing_learner: Optional[TimingLearner] = None,
        policy_recommender: Optional[PolicyRecommender] = None,
        signal_matcher: Optional[SignalActionMatcher] = None,
        require_approval: bool = False,
        on_position_update: Optional[Callable] = None,
        # 새로 추가
        symbol_filter: Optional[str] = None,  # 특정 심볼만 처리
        strategy_filter: Optional[str] = None  # 특정 전략만 처리
    ):
        # ... 기존 코드 ...
        self.symbol_filter = symbol_filter
        self.strategy_filter = strategy_filter
        
    async def process_signal(self, signal: Dict, ...):
        # 필터링 체크 추가
        if self.symbol_filter and signal.get('symbol') != self.symbol_filter:
            logger.debug(f"⏭️ 심볼 필터링: {signal.get('symbol')} != {self.symbol_filter}")
            return {'success': False, 'reason': 'symbol_filtered'}
        
        if self.strategy_filter and signal.get('strategy') != self.strategy_filter:
            logger.debug(f"⏭️ 전략 필터링: {signal.get('strategy')} != {self.strategy_filter}")
            return {'success': False, 'reason': 'strategy_filtered'}
        
        # ... 기존 처리 로직 ...
```

---

### 2. API 엔드포인트 수정

**파일**: `servers/web_server.py`

**변경사항**:
```python
@app.post("/api/paper/start")
async def start_paper_trading(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    # 기존 코드 ...
    initial_capital = request.get('initial_capital', 10000)
    require_approval = request.get('require_approval', False)
    
    # 새로 추가
    symbol_filter = request.get('symbol')  # 선택된 심볼
    strategy_filter = request.get('strategy')  # 선택된 전략
    
    # Paper Trading Engine 생성 시 필터 전달
    engine = PaperTradingEngine(
        broker=broker,
        signals_repo=signals_repo,
        events_repo=events_repo,
        positions_repo=positions_repo,
        timing_learner=timing_learner,
        policy_recommender=policy_recommender,
        signal_matcher=signal_matcher,
        require_approval=require_approval,
        on_position_update=on_position_update,
        symbol_filter=symbol_filter,  # 필터 추가
        strategy_filter=strategy_filter  # 필터 추가
    )
```

**새 엔드포인트**:
```python
@app.get("/api/paper/available-symbols")
async def get_available_symbols(user: dict = Depends(require_auth)):
    """사용 가능한 심볼 목록 조회"""
    # signals_repo에서 최근 신호가 있는 심볼들 조회
    symbols = signals_repo.get_unique_symbols(limit=100)
    return {"symbols": symbols}

@app.get("/api/paper/available-strategies")
async def get_available_strategies(user: dict = Depends(require_auth)):
    """사용 가능한 전략 목록 조회"""
    # signals_repo에서 최근 신호가 있는 전략들 조회
    strategies = signals_repo.get_unique_strategies(limit=50)
    return {"strategies": strategies}
```

---

### 3. UI 수정

**파일**: `static/paper_trading.html`

**변경사항**:

#### 3.1 Control Panel에 심볼/전략 선택 추가
```html
<div class="control-panel" id="control-panel">
    <!-- 기존 필드 -->
    <label>초기 자본:</label>
    <input type="number" id="initial-capital" value="10000" ...>
    
    <!-- 새로 추가: 심볼 선택 -->
    <label style="margin-left: 12px;">심볼:</label>
    <select id="symbol-select" style="padding: 6px 12px; ...">
        <option value="">전체 심볼</option>
        <!-- JavaScript로 동적 로드 -->
    </select>
    
    <!-- 새로 추가: 전략 선택 -->
    <label style="margin-left: 12px;">전략:</label>
    <select id="strategy-select" style="padding: 6px 12px; ...">
        <option value="">전체 전략</option>
        <!-- JavaScript로 동적 로드 -->
    </select>
    
    <label style="margin-left: 12px;">
        <input type="checkbox" id="require-approval" ...>
        사용자 승인 필요
    </label>
</div>
```

#### 3.2 JavaScript 수정
```javascript
// 사용 가능한 심볼/전략 로드
async function loadAvailableOptions() {
    try {
        // 심볼 목록
        const symbolsRes = await fetch('/api/paper/available-symbols', {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        const symbolsData = await symbolsRes.json();
        const symbolSelect = document.getElementById('symbol-select');
        symbolsData.symbols.forEach(symbol => {
            const option = document.createElement('option');
            option.value = symbol;
            option.textContent = symbol;
            symbolSelect.appendChild(option);
        });
        
        // 전략 목록
        const strategiesRes = await fetch('/api/paper/available-strategies', {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        const strategiesData = await strategiesRes.json();
        const strategySelect = document.getElementById('strategy-select');
        strategiesData.strategies.forEach(strategy => {
            const option = document.createElement('option');
            option.value = strategy;
            option.textContent = strategy;
            strategySelect.appendChild(option);
        });
    } catch (error) {
        console.error('옵션 로드 실패:', error);
    }
}

// 시작 버튼 클릭 시 선택된 심볼/전략 전달
async function startPaperTrading() {
    const initialCapital = parseFloat(document.getElementById('initial-capital').value);
    const requireApproval = document.getElementById('require-approval').checked;
    const selectedSymbol = document.getElementById('symbol-select').value;
    const selectedStrategy = document.getElementById('strategy-select').value;
    
    const response = await fetch('/api/paper/start', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
            initial_capital: initialCapital,
            require_approval: requireApproval,
            symbol: selectedSymbol || null,  // 선택된 심볼
            strategy: selectedStrategy || null  // 선택된 전략
        })
    });
    // ... 기존 코드 ...
}
```

---

### 4. SignalsRepo 확장 (선택사항)

**파일**: `database/signals_repo.py` (또는 해당 파일)

**추가 메서드**:
```python
def get_unique_symbols(self, limit: int = 100) -> List[str]:
    """고유 심볼 목록 조회"""
    query = """
        SELECT DISTINCT symbol 
        FROM tradebot_signals 
        WHERE created_at >= NOW() - INTERVAL '7 days'
        ORDER BY symbol
        LIMIT %s
    """
    result = pd.read_sql(query, self.db.engine, params=(limit,))
    return result['symbol'].tolist()

def get_unique_strategies(self, limit: int = 50) -> List[str]:
    """고유 전략 목록 조회"""
    query = """
        SELECT DISTINCT strategy 
        FROM tradebot_signals 
        WHERE created_at >= NOW() - INTERVAL '7 days'
        AND strategy IS NOT NULL
        ORDER BY strategy
        LIMIT %s
    """
    result = pd.read_sql(query, self.db.engine, params=(limit,))
    return result['strategy'].tolist()
```

---

## 🔄 워크플로우

```
1. 사용자가 UI에서 심볼과 전략 선택
   예: BTCUSDT, "ICT_Strategy"

2. "시작" 버튼 클릭
   → /api/paper/start에 symbol, strategy 전달

3. PaperTradingEngine 생성 시 필터 설정
   → symbol_filter = "BTCUSDT"
   → strategy_filter = "ICT_Strategy"

4. WebSocket으로 신호 수신
   → 모든 신호가 들어오지만

5. process_signal에서 필터링
   → BTCUSDT + ICT_Strategy 신호만 처리
   → 나머지는 무시

6. 필터링된 신호만 자동 진입/청산
```

---

## 📊 UI 개선안

### 모드 선택 추가 (선택사항)

```html
<div class="mode-selector">
    <label>
        <input type="radio" name="trading-mode" value="all" checked>
        전체 모드 (모든 심볼/전략)
    </label>
    <label>
        <input type="radio" name="trading-mode" value="single">
        단일 모드 (심볼/전략 선택)
    </label>
</div>

<div id="single-mode-panel" style="display:none;">
    <select id="symbol-select">...</select>
    <select id="strategy-select">...</select>
</div>
```

---

## ✅ 구현 체크리스트

### Backend
- [ ] `PaperTradingEngine.__init__`에 `symbol_filter`, `strategy_filter` 파라미터 추가
- [ ] `process_signal`에서 필터링 로직 추가
- [ ] `/api/paper/start` 엔드포인트에 `symbol`, `strategy` 파라미터 추가
- [ ] `/api/paper/available-symbols` 엔드포인트 추가
- [ ] `/api/paper/available-strategies` 엔드포인트 추가
- [ ] `SignalsRepo`에 `get_unique_symbols`, `get_unique_strategies` 메서드 추가

### Frontend
- [ ] Control Panel에 심볼/전략 선택 드롭다운 추가
- [ ] `loadAvailableOptions()` 함수 구현
- [ ] `startPaperTrading()`에서 선택값 전달
- [ ] 페이지 로드 시 옵션 자동 로드
- [ ] (선택사항) 모드 선택 라디오 버튼 추가

### 테스트
- [ ] 단일 심볼 선택 시 해당 심볼 신호만 처리되는지 확인
- [ ] 단일 전략 선택 시 해당 전략 신호만 처리되는지 확인
- [ ] 둘 다 선택 시 교집합만 처리되는지 확인
- [ ] 선택 안 하면 기존처럼 전체 처리되는지 확인

---

## 🚀 구현 순서

1. **Backend 필터링 로직** (30분)
   - `PaperTradingEngine` 수정
   - `process_signal` 필터링 추가

2. **API 엔드포인트** (20분)
   - `/api/paper/start` 수정
   - `/api/paper/available-symbols` 추가
   - `/api/paper/available-strategies` 추가

3. **SignalsRepo 확장** (15분)
   - `get_unique_symbols` 추가
   - `get_unique_strategies` 추가

4. **UI 구현** (30분)
   - 드롭다운 추가
   - JavaScript 로직 추가

5. **테스트** (20분)
   - 각 시나리오 테스트

**총 예상 시간: 약 2시간**

---

## 💡 추가 개선 아이디어

1. **실시간 모니터링 강화**
   - 선택한 심볼의 실시간 차트 표시
   - 전략 신호 발생 시 시각적 알림

2. **통계 표시**
   - 선택한 심볼/전략의 성과만 표시
   - 승률, 평균 수익률 등

3. **자동 재시작**
   - 심볼/전략 변경 시 자동으로 재시작 옵션

---

**이 방식으로 구현하면 기존 기능을 유지하면서 단일 심볼/전략 모드를 추가할 수 있습니다!**
