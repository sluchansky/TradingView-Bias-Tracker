-- Central Ghost Coordinator — optional Phase 1 shadow evidence persistence.
-- Apply via the Replit DB tool in development and the Publish schema diff for
-- production. NEVER run this DDL from app.py.
-- These tables do not modify or replace any legacy research ledger.

CREATE TABLE IF NOT EXISTS ghost_coordinator_observations (
    observation_id        TEXT PRIMARY KEY,
    market_opportunity_id TEXT NOT NULL,
    source_system         TEXT NOT NULL,
    source_event_id       TEXT NOT NULL,
    instrument            TEXT NOT NULL,
    timeframe             TEXT NOT NULL,
    setup_family          TEXT NOT NULL,
    strategy_name         TEXT NOT NULL,
    strategy_version      TEXT NOT NULL,
    direction             TEXT NOT NULL,
    signal_time           TIMESTAMPTZ NOT NULL,
    source_bar_time       TEXT NOT NULL,
    entry_price           NUMERIC NOT NULL,
    stop_price            NUMERIC NOT NULL,
    targets               JSONB NOT NULL,
    context               JSONB NOT NULL DEFAULT '{}'::jsonb,
    experiment_variant    TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gco_market_opportunity
    ON ghost_coordinator_observations (market_opportunity_id);
CREATE INDEX IF NOT EXISTS idx_gco_source_system
    ON ghost_coordinator_observations (source_system, created_at DESC);

CREATE TABLE IF NOT EXISTS ghost_coordinator_telemetry_events (
    telemetry_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    event_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);