-- 감시 심볼 테이블
-- 자동 분석할 심볼 목록을 저장

CREATE TABLE IF NOT EXISTS watched_symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT '15m',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- 심볼은 유니크해야 함
    UNIQUE(symbol)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_watched_symbols_enabled ON watched_symbols(enabled);
CREATE INDEX IF NOT EXISTS idx_watched_symbols_symbol ON watched_symbols(symbol);

-- 기본 감시 심볼 추가
INSERT INTO watched_symbols (symbol, timeframe, enabled)
VALUES
    ('BTCUSDT', '15m', true),
    ('ETHUSDT', '15m', true),
    ('SOLUSDT', '15m', true)
ON CONFLICT (symbol) DO NOTHING;

-- 업데이트 트리거
CREATE OR REPLACE FUNCTION update_watched_symbols_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_watched_symbols_updated_at ON watched_symbols;
CREATE TRIGGER trg_watched_symbols_updated_at
    BEFORE UPDATE ON watched_symbols
    FOR EACH ROW
    EXECUTE FUNCTION update_watched_symbols_timestamp();
