-- Persistent market-thesis continuity.
-- Application code performs INSERT/SELECT only; apply this schema in development
-- and let Replit Publish manage any later production schema diff.

CREATE TABLE IF NOT EXISTS persistent_market_theses (
    instrument          TEXT        NOT NULL,
    mode                TEXT        NOT NULL,
    thesis_id           TEXT        NOT NULL,
    direction           TEXT,
    status              TEXT        NOT NULL,
    confidence          INTEGER     NOT NULL DEFAULT 0,
    last_evidence_id    TEXT,
    data                JSONB       NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (instrument, mode),
    CHECK (mode IN ('SCALP', 'INTRADAY_TREND')),
    CHECK (status IN (
        'NEUTRAL', 'FORMING', 'CONFIRMED', 'WEAKENING',
        'PENDING_REVERSAL', 'INVALIDATED'
    )),
    CHECK (confidence BETWEEN 0 AND 100)
);

CREATE TABLE IF NOT EXISTS persistent_thesis_events (
    event_id            TEXT        PRIMARY KEY,
    instrument          TEXT        NOT NULL,
    mode                TEXT        NOT NULL,
    thesis_id           TEXT        NOT NULL,
    previous_thesis_id  TEXT,
    previous_status     TEXT,
    status              TEXT        NOT NULL,
    direction           TEXT,
    confidence          INTEGER     NOT NULL DEFAULT 0,
    evidence_id         TEXT,
    data                JSONB       NOT NULL,
    occurred_at         TIMESTAMPTZ NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (mode IN ('SCALP', 'INTRADAY_TREND')),
    CHECK (status IN (
        'NEUTRAL', 'FORMING', 'CONFIRMED', 'WEAKENING',
        'PENDING_REVERSAL', 'INVALIDATED'
    )),
    CHECK (confidence BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_persistent_thesis_events_lookup
    ON persistent_thesis_events (instrument, mode, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_persistent_thesis_events_thesis
    ON persistent_thesis_events (thesis_id, occurred_at DESC);