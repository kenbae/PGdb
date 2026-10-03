-- marketdb 초기 스키마
-- 소스의 INSERT/ON CONFLICT 및 tradeBot SQL과 맞춤

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------
-- 파이프라인 핵심
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candles (
    id              BIGSERIAL PRIMARY KEY,
    exchange        VARCHAR(32) NOT NULL DEFAULT 'binance',
    symbol          VARCHAR(32) NOT NULL,
    market          VARCHAR(16) NOT NULL DEFAULT 'usdtm',
    tf              VARCHAR(10) NOT NULL,
    open_time       TIMESTAMPTZ NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          DOUBLE PRECISION NOT NULL,
    quote_volume    DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exchange, symbol, market, tf, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time
    ON candles (symbol, tf, open_time DESC);

CREATE TABLE IF NOT EXISTS indicators (
    id              BIGSERIAL PRIMARY KEY,
    symbol          VARCHAR(32) NOT NULL,
    tf              VARCHAR(10) NOT NULL,
    open_time       TIMESTAMPTZ NOT NULL,
    ema20           DOUBLE PRECISION,
    ema50           DOUBLE PRECISION,
    ema200          DOUBLE PRECISION,
    vwap            DOUBLE PRECISION,
    rsi             DOUBLE PRECISION,
    atr             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, tf, open_time)
);

CREATE INDEX IF NOT EXISTS idx_indicators_symbol_tf_time
    ON indicators (symbol, tf, open_time DESC);

CREATE TABLE IF NOT EXISTS signals (
    signal_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(32) NOT NULL,
    tf              VARCHAR(10) NOT NULL,
    open_time       TIMESTAMPTZ NOT NULL,
    strategy        VARCHAR(64) NOT NULL,
    direction       VARCHAR(16) NOT NULL,
    entry           DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION,
    take_profit_1   DOUBLE PRECISION,
    take_profit_2   DOUBLE PRECISION,
    score           INTEGER,
    reason_json     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, tf, open_time, strategy, direction)
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_tf_time
    ON signals (symbol, tf, open_time DESC);
CREATE INDEX IF NOT EXISTS idx_signals_strategy
    ON signals (strategy);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id       UUID PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
    exit_time       TIMESTAMPTZ,
    exit_price      DOUBLE PRECISION,
    result          VARCHAR(32),
    r_multiple      DOUBLE PRECISION,
    mfe             DOUBLE PRECISION,
    mae             DOUBLE PRECISION,
    evaluated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_result ON outcomes (result);
CREATE INDEX IF NOT EXISTS idx_outcomes_exit_time ON outcomes (exit_time DESC);

CREATE TABLE IF NOT EXISTS llm_reports (
    id              BIGSERIAL PRIMARY KEY,
    event_key       TEXT NOT NULL,
    stage           TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    prompt          TEXT,
    response_json   JSONB,
    ok              BOOLEAN,
    error           TEXT,
    symbol          VARCHAR(32),
    tf              VARCHAR(10),
    open_time       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_reports_event
    ON llm_reports (event_key, stage, model, prompt_version);

CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_reports_candle
    ON llm_reports (symbol, tf, open_time, stage, model, prompt_version)
    WHERE symbol IS NOT NULL AND tf IS NOT NULL AND open_time IS NOT NULL;

CREATE TABLE IF NOT EXISTS llm_report_embeddings (
    report_id       BIGINT NOT NULL REFERENCES llm_reports(id) ON DELETE CASCADE,
    embed_model     TEXT NOT NULL,
    dim             INTEGER,
    faiss_id        INTEGER,
    PRIMARY KEY (report_id, embed_model)
);

CREATE TABLE IF NOT EXISTS ema_signals (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       VARCHAR(128) UNIQUE NOT NULL,
    symbol          VARCHAR(64) NOT NULL,
    timeframe       VARCHAR(10) NOT NULL,
    direction       VARCHAR(16) NOT NULL,
    signal_time     TIMESTAMPTZ NOT NULL,
    bar_open_time   TIMESTAMPTZ NOT NULL,
    ema20           DOUBLE PRECISION,
    ema50           DOUBLE PRECISION,
    ema100          DOUBLE PRECISION,
    ema20_prev      DOUBLE PRECISION,
    ema50_prev      DOUBLE PRECISION,
    close_price     DOUBLE PRECISION,
    atr             DOUBLE PRECISION,
    vwap            DOUBLE PRECISION,
    prob_long       DOUBLE PRECISION,
    prob_short      DOUBLE PRECISION,
    prob_samples    INTEGER,
    entry_price     DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION,
    take_profit_1   DOUBLE PRECISION,
    take_profit_2   DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ema_signals_time
    ON ema_signals (signal_time DESC);
