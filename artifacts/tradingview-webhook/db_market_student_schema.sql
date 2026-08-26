-- Persistent market-student research ledger.
-- Apply in development with the database tool and through the production
-- Publish schema diff. app.py intentionally performs no DDL.
-- This schema is additive and has no relationship to execution tables.

CREATE TABLE IF NOT EXISTS market_student_observations (
    observation_id    TEXT PRIMARY KEY,
    instrument        TEXT NOT NULL,
    mode              TEXT NOT NULL,
    source_system     TEXT NOT NULL,
    source_event_id   TEXT NOT NULL,
    source_timestamp  TIMESTAMPTZ NOT NULL,
    fingerprint       TEXT NOT NULL,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument, mode, source_system, source_event_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_mso_inst_mode_ts
    ON market_student_observations (instrument, mode, created_at DESC);

CREATE TABLE IF NOT EXISTS market_student_hypotheses (
    hypothesis_id     TEXT PRIMARY KEY,
    observation_id    TEXT NOT NULL,
    instrument        TEXT NOT NULL,
    mode              TEXT NOT NULL,
    contract          JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_msh_unresolved
    ON market_student_hypotheses (instrument, mode, created_at DESC);

CREATE TABLE IF NOT EXISTS market_student_outcomes (
    outcome_id        TEXT PRIMARY KEY,
    hypothesis_id     TEXT NOT NULL,
    status            TEXT NOT NULL,
    normalized        JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_values     JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance        JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason            TEXT,
    resolved_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mso_outcomes_hypothesis
    ON market_student_outcomes (hypothesis_id, resolved_at DESC);

CREATE TABLE IF NOT EXISTS market_student_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    source_system     TEXT NOT NULL,
    source_record_id  TEXT NOT NULL,
    hypothesis_id     TEXT NOT NULL,
    instrument        TEXT NOT NULL,
    mode              TEXT NOT NULL,
    provenance        JSONB NOT NULL DEFAULT '{}'::jsonb,
    matched           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, source_record_id, hypothesis_id)
);
CREATE INDEX IF NOT EXISTS idx_msr_source
    ON market_student_reconciliations (source_system, source_record_id);

CREATE TABLE IF NOT EXISTS market_student_thesis_states (
    thesis_id         TEXT PRIMARY KEY,
    instrument        TEXT NOT NULL,
    mode              TEXT NOT NULL,
    state             TEXT NOT NULL,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument, mode)
);

CREATE TABLE IF NOT EXISTS market_student_ready_alerts (
    dedupe_key         TEXT PRIMARY KEY,
    instrument         TEXT NOT NULL,
    mode               TEXT NOT NULL,
    hypothesis_id      TEXT NOT NULL,
    delivered          BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_error     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at       TIMESTAMPTZ
);
