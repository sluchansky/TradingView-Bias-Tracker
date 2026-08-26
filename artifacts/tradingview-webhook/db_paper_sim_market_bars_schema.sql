-- Bounded durable Databento completed bars for paper-simulation restart recovery.
-- Apply to development through the database schema flow; production receives the
-- additive table through Publish diff. app.py intentionally performs no DDL.

CREATE TABLE IF NOT EXISTS paper_sim_market_bars (
    instrument   TEXT NOT NULL,
    bar_start    TIMESTAMPTZ NOT NULL,
    open         DOUBLE PRECISION NOT NULL,
    high         DOUBLE PRECISION NOT NULL,
    low          DOUBLE PRECISION NOT NULL,
    close        DOUBLE PRECISION NOT NULL,
    volume       BIGINT NOT NULL DEFAULT 0,
    source       TEXT NOT NULL,
    capture_session_id          TEXT NOT NULL,
    capture_session_started_at  TIMESTAMPTZ NOT NULL,
    capture_sequence            BIGINT NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instrument, bar_start),
    CHECK (high >= low),
    CHECK (open >= low AND open <= high),
    CHECK (close >= low AND close <= high),
    CHECK (volume >= 0),
    CHECK (capture_sequence > 0),
    UNIQUE (capture_session_id, instrument, capture_sequence)
);

CREATE INDEX IF NOT EXISTS idx_paper_sim_market_bars_start
    ON paper_sim_market_bars (bar_start);