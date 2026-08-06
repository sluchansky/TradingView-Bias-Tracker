-- Volatility Intelligence — observation persistence schema
-- Run via the Replit database tool (dev) then re-publish (prod).
-- app.py runs NO DDL — this file is the authoritative schema source.
-- All tables use IF NOT EXISTS so re-running is safe.

-- Stores periodic VIX snapshots for historical analysis and replay.
-- INSERT-only from the background thread; app.py never runs DDL.
CREATE TABLE IF NOT EXISTS volatility_observations (
    id                  SERIAL          PRIMARY KEY,
    recorded_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    source              TEXT            NOT NULL DEFAULT 'alpha_vantage',
    vix_price           NUMERIC(8,3),
    vix_previous_close  NUMERIC(8,3),
    vix_change          NUMERIC(8,3),
    vix_change_pct      NUMERIC(8,4),
    vix_session_high    NUMERIC(8,3),
    vix_session_low     NUMERIC(8,3),
    regime              TEXT,           -- CALM / NORMAL / ELEVATED / EXTREME / UNKNOWN
    direction           TEXT,           -- RISING / FALLING / FLAT / UNKNOWN
    velocity            TEXT,           -- SLOW / MODERATE / FAST / UNKNOWN
    acceleration        TEXT,           -- INCREASING / DECREASING / STABLE / UNKNOWN
    risk_tone           TEXT,           -- RISK_ON / NEUTRAL / RISK_OFF_PRESSURE / RISK_OFF_SHOCK / UNKNOWN
    equity_context      TEXT,           -- HEADWIND_FOR_LONGS / TAILWIND_FOR_LONGS / MIXED / NEUTRAL / UNKNOWN
    session_percentile  NUMERIC(5,1),
    confidence          INTEGER,
    data_status         TEXT,           -- LIVE / DELAYED / STALE / UNAVAILABLE / ERROR
    is_delayed          BOOLEAN         NOT NULL DEFAULT TRUE,
    age_seconds         NUMERIC(8,1),
    change_5m           NUMERIC(8,3),
    change_15m          NUMERIC(8,3),
    change_30m          NUMERIC(8,3),
    raw_snapshot        JSONB
);

-- Index for time-series queries
CREATE INDEX IF NOT EXISTS idx_volatility_observations_recorded_at
    ON volatility_observations (recorded_at DESC);

-- Index for regime queries
CREATE INDEX IF NOT EXISTS idx_volatility_observations_regime
    ON volatility_observations (regime, recorded_at DESC);

-- Keep 30 days of observations (maintenance job; run manually or schedule)
-- DELETE FROM volatility_observations WHERE recorded_at < NOW() - INTERVAL '30 days';
