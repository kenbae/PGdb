-- 적응형 전략 선택 시스템을 위한 테이블 생성

-- 1. strategy_performance 테이블 (전략 성과)
CREATE TABLE IF NOT EXISTS strategy_performance (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    
    -- 성과 지표 (최근 N개 거래 기준)
    period_type VARCHAR(10) NOT NULL,  -- 'short' (10), 'medium' (30), 'long' (100)
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

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_strategy_performance_symbol ON strategy_performance(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_performance_strategy ON strategy_performance(strategy_name);
CREATE INDEX IF NOT EXISTS idx_strategy_performance_score ON strategy_performance(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_performance_symbol_timeframe ON strategy_performance(symbol, timeframe);

-- 2. strategy_switches 테이블 (전략 전환 기록)
CREATE TABLE IF NOT EXISTS strategy_switches (
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

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_strategy_switches_symbol ON strategy_switches(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_switches_created ON strategy_switches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_switches_symbol_timeframe ON strategy_switches(symbol, timeframe);

-- 업데이트 트리거
CREATE OR REPLACE FUNCTION update_strategy_performance_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_strategy_performance_updated_at ON strategy_performance;
CREATE TRIGGER trg_strategy_performance_updated_at
    BEFORE UPDATE ON strategy_performance
    FOR EACH ROW
    EXECUTE FUNCTION update_strategy_performance_timestamp();
