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

-- Canonical Ghost Phase 1 reconciliation sidecar.  This is append-only shadow
-- telemetry over legacy observations; it never replaces a legacy writer or
-- outcome resolver.  Apply through the development DB tool / Publish schema
-- diff only, never from app.py.
CREATE TABLE IF NOT EXISTS canonical_ghost_reconciliation_events (
    event_id                         TEXT PRIMARY KEY,
    canonical_opportunity_id         TEXT NOT NULL,
    canonical_observation_id         TEXT NOT NULL,
    coordinator_market_opportunity_id TEXT NOT NULL,
    trading_mode                     TEXT NOT NULL,
    source_system                    TEXT NOT NULL,
    source_record_id                 TEXT NOT NULL,
    event_type                       TEXT NOT NULL,
    legacy_table                     TEXT NOT NULL,
    raw_status                       TEXT,
    raw_close_reason                 TEXT,
    normalized_outcome               TEXT NOT NULL,
    gross_r                          NUMERIC,
    cost_r                           NUMERIC,
    net_r                            NUMERIC,
    result_r                         NUMERIC,
    exit_price                       NUMERIC,
    mfe_r                            NUMERIC,
    mae_r                            NUMERIC,
    bars_held                        INTEGER,
    event_at                         TIMESTAMPTZ,
    payload                          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cgr_canonical_opportunity
    ON canonical_ghost_reconciliation_events (canonical_opportunity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cgr_legacy_source
    ON canonical_ghost_reconciliation_events (source_system, source_record_id, created_at);

-- One durable projection per eligible generic ghost result. This table is a
-- shadow-only evidence boundary: it links exact legacy, coordinator, and
-- canonical identities, while the generic ghost lifecycle remains the sole
-- outcome authority. Terminal state is an in-place deterministic snapshot;
-- the append-only reconciliation table above retains copied event history.
CREATE TABLE IF NOT EXISTS canonical_ghost_evidence_records (
    evidence_id                         TEXT PRIMARY KEY,
    canonical_opportunity_id            TEXT NOT NULL,
    canonical_observation_id            TEXT NOT NULL,
    coordinator_market_opportunity_id   TEXT NOT NULL,
    coordinator_observation_id          TEXT NOT NULL,
    trading_mode                        TEXT NOT NULL
        CHECK (trading_mode IN ('SCALP', 'INTRADAY_TREND')),
    source_system                       TEXT NOT NULL
        CHECK (source_system = 'generic_ghost'),
    source_result_id                    TEXT NOT NULL,
    legacy_table                        TEXT NOT NULL,
    strategy_name                       TEXT NOT NULL,
    strategy_version                    TEXT NOT NULL,
    setup_family                        TEXT NOT NULL,
    instrument                          TEXT,
    timeframe                           TEXT,
    direction                           TEXT,
    signal_time                         TIMESTAMPTZ,
    source_bar_time                     TEXT,
    entry_price                         NUMERIC,
    stop_price                          NUMERIC,
    targets                             JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_state                        TEXT NOT NULL DEFAULT 'OBSERVED',
    outcome_version                     TEXT,
    outcome_order_key                   TEXT NOT NULL DEFAULT '',
    raw_status                          TEXT,
    raw_close_reason                    TEXT,
    normalized_outcome                  TEXT NOT NULL DEFAULT 'OPEN',
    gross_r                             NUMERIC,
    cost_r                              NUMERIC,
    net_r                               NUMERIC,
    result_r                            NUMERIC,
    exit_price                          NUMERIC,
    mfe_r                               NUMERIC,
    mae_r                               NUMERIC,
    bars_held                           INTEGER,
    outcome_at                          TIMESTAMPTZ,
    context                             JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome_payload                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trading_mode, source_system, source_result_id)
);
-- The columns below are additive metadata for strict replay/provenance checks.
-- Defaults keep the migration non-destructive for the Phase-1 rows already
-- present in this table; the application only treats fully upgraded schemas as
-- persistence-ready.
ALTER TABLE canonical_ghost_evidence_records
    ADD COLUMN IF NOT EXISTS record_kind TEXT NOT NULL DEFAULT 'MATCHED',
    ADD COLUMN IF NOT EXISTS evidence_schema_version TEXT NOT NULL DEFAULT '2',
    ADD COLUMN IF NOT EXISTS provenance_version TEXT NOT NULL
        DEFAULT 'canonical-evidence-provenance-v1',
    ADD COLUMN IF NOT EXISTS provenance_fingerprint TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS resolver_name TEXT NOT NULL
        DEFAULT 'generic_ghost_observation_lifecycle',
    ADD COLUMN IF NOT EXISTS resolver_version TEXT NOT NULL
        DEFAULT 'legacy-generic-ghost-v1',
    ADD COLUMN IF NOT EXISTS outcome_schema_version TEXT NOT NULL
        DEFAULT 'canonical-outcome-v1';
CREATE INDEX IF NOT EXISTS idx_cge_mode_outcome_time
    ON canonical_ghost_evidence_records (trading_mode, outcome_at DESC);
CREATE INDEX IF NOT EXISTS idx_cge_coordinator_observation
    ON canonical_ghost_evidence_records (coordinator_observation_id);

-- Explicit durable representation for a canonical result that cannot yet be
-- linked to the exact generic-ghost/coordinator identity chain. This is an
-- evidence record, not a resolver or outcome authority. A later exact match
-- records matched_evidence_id in place; the unmatched fact is never deleted.
CREATE TABLE IF NOT EXISTS canonical_ghost_unmatched_evidence_records (
    unmatched_id                       TEXT PRIMARY KEY,
    record_kind                        TEXT NOT NULL DEFAULT 'UNMATCHED'
        CHECK (record_kind = 'UNMATCHED'),
    evidence_schema_version            TEXT NOT NULL DEFAULT '2',
    provenance_version                 TEXT NOT NULL
        DEFAULT 'canonical-evidence-provenance-v1',
    provenance_fingerprint             TEXT NOT NULL,
    resolver_name                      TEXT NOT NULL
        DEFAULT 'generic_ghost_observation_lifecycle',
    resolver_version                   TEXT NOT NULL
        DEFAULT 'legacy-generic-ghost-v1',
    outcome_schema_version             TEXT NOT NULL
        DEFAULT 'canonical-outcome-v1',
    trading_mode                       TEXT NOT NULL
        CHECK (trading_mode IN ('SCALP', 'INTRADAY_TREND')),
    source_system                      TEXT NOT NULL,
    source_result_id                   TEXT,
    result_identity                    TEXT NOT NULL,
    canonical_opportunity_id           TEXT,
    canonical_observation_id           TEXT,
    coordinator_market_opportunity_id  TEXT,
    coordinator_observation_id         TEXT,
    legacy_table                       TEXT NOT NULL,
    strategy_name                      TEXT NOT NULL,
    strategy_version                   TEXT NOT NULL,
    setup_family                      TEXT NOT NULL,
    instrument                         TEXT,
    timeframe                          TEXT,
    direction                          TEXT,
    signal_time                        TIMESTAMPTZ,
    source_bar_time                    TEXT,
    entry_price                        NUMERIC,
    stop_price                         NUMERIC,
    targets                            JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_state                      TEXT NOT NULL DEFAULT 'UNMATCHED'
        CHECK (result_state = 'UNMATCHED'),
    unmatched_reason                   TEXT NOT NULL,
    matched_evidence_id                TEXT,
    raw_status                         TEXT,
    raw_close_reason                   TEXT,
    normalized_outcome                 TEXT NOT NULL DEFAULT 'UNKNOWN',
    gross_r                            NUMERIC,
    cost_r                             NUMERIC,
    net_r                              NUMERIC,
    result_r                           NUMERIC,
    exit_price                         NUMERIC,
    mfe_r                              NUMERIC,
    mae_r                              NUMERIC,
    bars_held                          INTEGER,
    outcome_at                         TIMESTAMPTZ,
    context                             JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome_payload                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trading_mode, source_system, result_identity)
);
CREATE INDEX IF NOT EXISTS idx_cgu_mode_created
    ON canonical_ghost_unmatched_evidence_records (trading_mode, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cgu_source_identity
    ON canonical_ghost_unmatched_evidence_records (source_system, result_identity);

-- Enforce all future links while keeping this additive migration safe for any
-- pre-existing historical rows. Restore still audits old rows explicitly.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_cgu_matched_evidence'
          AND conrelid = 'canonical_ghost_unmatched_evidence_records'::regclass
    ) THEN
        ALTER TABLE canonical_ghost_unmatched_evidence_records
            ADD CONSTRAINT fk_cgu_matched_evidence
            FOREIGN KEY (matched_evidence_id)
            REFERENCES canonical_ghost_evidence_records (evidence_id)
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
    END IF;
END $$;