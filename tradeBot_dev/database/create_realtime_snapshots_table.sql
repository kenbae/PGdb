-- 실행 종목 지표 스냅샷 (매 정시, 15분, 30분, 45분 저장)
-- 진입/청산 정보(position_snapshot, exit_info) 포함

CREATE TABLE IF NOT EXISTS realtime_indicator_snapshots (
    id                  SERIAL PRIMARY KEY,
    snapshot_time       TIMESTAMP WITH TIME ZONE NOT NULL,
    engine_id           VARCHAR(128) NOT NULL,
    symbol              VARCHAR(32) NOT NULL,
    strategy            VARCHAR(64) NOT NULL,
    timeframe           VARCHAR(16) NOT NULL,
    current_price       NUMERIC(20, 8),
    ema_20              NUMERIC(20, 8),
    ema_50              NUMERIC(20, 8),
    alignment           VARCHAR(16),
    rsi                 NUMERIC(8, 2),
    ema_spread          NUMERIC(10, 4),
    entry_score         NUMERIC(5, 2),
    position_snapshot   JSONB,
    exit_info           JSONB,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_time ON realtime_indicator_snapshots(snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_engine ON realtime_indicator_snapshots(engine_id);
CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_symbol ON realtime_indicator_snapshots(symbol);

COMMENT ON TABLE realtime_indicator_snapshots IS '실시간 실행 종목 지표 스냅샷 (매 정시/15/30/45분) + 진입/청산 정보';
