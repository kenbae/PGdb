-- 포지션 히스토리 테이블
-- 바이낸스 계좌의 실제 거래 포지션 기록

CREATE TABLE IF NOT EXISTS tradebot_positions (
    id SERIAL PRIMARY KEY,

    -- 포지션 ID
    position_id VARCHAR(50) UNIQUE NOT NULL,  -- order_id 또는 고유 ID

    -- 기본 정보
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'buy' or 'sell' (LONG or SHORT)

    -- 가격 정보
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,  -- 수량

    -- 손익 정보
    pnl DECIMAL(20, 8),  -- 실현 손익 (USDT)
    pnl_percent DECIMAL(10, 4),  -- 손익률 (%)
    commission DECIMAL(20, 8),  -- 수수료

    -- 포지션 상태
    status VARCHAR(20) NOT NULL,  -- 'open', 'closed', 'liquidated'

    -- 레버리지 및 마진
    leverage INTEGER,  -- 레버리지 배수
    margin DECIMAL(20, 8),  -- 사용 마진

    -- 시간 정보
    open_time TIMESTAMP NOT NULL,
    close_time TIMESTAMP,
    duration_minutes INTEGER,  -- 포지션 지속 시간 (분)

    -- 메타데이터
    order_ids JSONB,  -- 관련 주문 ID들
    metadata JSONB,  -- 추가 정보

    -- 바이낸스 원본 데이터
    raw_data JSONB,

    -- 데이터 출처
    source VARCHAR(20) DEFAULT 'binance',  -- 'binance', 'csv', 'manual'

    -- 타임스탬프
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_symbol ON tradebot_positions(symbol);
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_side ON tradebot_positions(side);
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_status ON tradebot_positions(status);
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_open_time ON tradebot_positions(open_time DESC);
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_close_time ON tradebot_positions(close_time DESC);
CREATE INDEX IF NOT EXISTS idx_tradebot_positions_pnl ON tradebot_positions(pnl DESC);

-- 업데이트 트리거
CREATE OR REPLACE FUNCTION update_tradebot_positions_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tradebot_positions_updated_at ON tradebot_positions;
CREATE TRIGGER trg_tradebot_positions_updated_at
    BEFORE UPDATE ON tradebot_positions
    FOR EACH ROW
    EXECUTE FUNCTION update_tradebot_positions_timestamp();
