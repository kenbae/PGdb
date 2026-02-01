-- 실시간 신호 저장 테이블
-- ICT + AI 전략에서 생성된 모든 신호를 저장

CREATE TABLE IF NOT EXISTS tradebot_signals (
    id SERIAL PRIMARY KEY,

    -- 신호 기본 정보
    signal_id VARCHAR(50) UNIQUE NOT NULL,  -- 고유 ID (symbol_timestamp)
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    signal_type VARCHAR(10) NOT NULL,  -- 'buy' or 'sell'

    -- 가격 정보
    entry_price DECIMAL(20, 8) NOT NULL,
    stop_loss DECIMAL(20, 8) NOT NULL,
    take_profit_1 DECIMAL(20, 8),
    take_profit_2 DECIMAL(20, 8),

    -- 신뢰도 및 리스크
    confidence DECIMAL(5, 4),  -- 0.0 ~ 1.0
    risk_reward DECIMAL(10, 4),

    -- AI 분석 정보
    ai_decision VARCHAR(20),  -- 'approve', 'reject', 'caution'
    ai_confidence DECIMAL(5, 4),  -- 0.0 ~ 1.0
    ai_reasoning TEXT,
    ai_risk_assessment TEXT,
    ai_market_context TEXT,

    -- 근거 (JSON)
    reasons JSONB,
    metadata JSONB,

    -- 실행 여부
    executed BOOLEAN DEFAULT false,
    execution_time TIMESTAMP,
    execution_price DECIMAL(20, 8),

    -- 포지션 매칭
    matched_position_id VARCHAR(50),  -- 매칭된 실제 포지션 ID (tradebot_positions.position_id)
    matched_at TIMESTAMP,  -- 매칭된 시간

    -- 타임스탬프
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_symbol ON tradebot_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_timeframe ON tradebot_signals(timeframe);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_signal_type ON tradebot_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_created_at ON tradebot_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_executed ON tradebot_signals(executed);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_ai_decision ON tradebot_signals(ai_decision);
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_matched_position_id ON tradebot_signals(matched_position_id);

-- 신호 결과 테이블
CREATE TABLE IF NOT EXISTS tradebot_signal_results (
    id SERIAL PRIMARY KEY,
    signal_id VARCHAR(50) UNIQUE NOT NULL,

    -- 결과
    status VARCHAR(20) NOT NULL,  -- 'pending', 'tp1_hit', 'tp2_hit', 'sl_hit', 'expired', 'manual_close'
    exit_price DECIMAL(20, 8),
    exit_time TIMESTAMP,

    -- 성과 지표
    pnl DECIMAL(20, 8),  -- Profit/Loss
    pnl_percent DECIMAL(10, 4),  -- %
    r_multiple DECIMAL(10, 4),  -- R-multiple (pnl / risk)

    -- 최대/최소
    max_favorable_excursion DECIMAL(10, 4),  -- MFE %
    max_adverse_excursion DECIMAL(10, 4),  -- MAE %

    -- 지속 시간
    duration_minutes INTEGER,

    -- 시뮬레이션 여부
    is_simulation BOOLEAN DEFAULT true,

    -- 메타데이터
    notes TEXT,

    -- 타임스탬프
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- 외래키
    FOREIGN KEY (signal_id) REFERENCES tradebot_signals(signal_id) ON DELETE CASCADE
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_tradebot_signal_results_status ON tradebot_signal_results(status);
CREATE INDEX IF NOT EXISTS idx_tradebot_signal_results_is_simulation ON tradebot_signal_results(is_simulation);
CREATE INDEX IF NOT EXISTS idx_tradebot_signal_results_pnl ON tradebot_signal_results(pnl DESC);
CREATE INDEX IF NOT EXISTS idx_tradebot_signal_results_exit_time ON tradebot_signal_results(exit_time DESC);

-- 업데이트 트리거 (tradebot_signals)
CREATE OR REPLACE FUNCTION update_tradebot_signals_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tradebot_signals_updated_at ON tradebot_signals;
CREATE TRIGGER trg_tradebot_signals_updated_at
    BEFORE UPDATE ON tradebot_signals
    FOR EACH ROW
    EXECUTE FUNCTION update_tradebot_signals_timestamp();

-- 업데이트 트리거 (tradebot_signal_results)
CREATE OR REPLACE FUNCTION update_tradebot_signal_results_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tradebot_signal_results_updated_at ON tradebot_signal_results;
CREATE TRIGGER trg_tradebot_signal_results_updated_at
    BEFORE UPDATE ON tradebot_signal_results
    FOR EACH ROW
    EXECUTE FUNCTION update_tradebot_signal_results_timestamp();
