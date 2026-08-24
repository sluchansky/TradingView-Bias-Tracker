-- P4 append-only final-result observer. Apply with the DB tool / Publish diff.
-- app.py contains no DDL and this table is never a trading input.
CREATE TABLE IF NOT EXISTS authoritative_verdict_history (
    event_id                  BIGSERIAL PRIMARY KEY,
    observation_key           TEXT NOT NULL UNIQUE,
    previous_observation_key TEXT,
    snapshot_hash             TEXT NOT NULL,
    instrument                TEXT NOT NULL CHECK (instrument IN ('MGC', 'MNQ', 'MES', 'MYM')),
    mode                      TEXT NOT NULL CHECK (mode IN ('SCALP', 'INTRADAY_TREND')),
    candidate_direction       TEXT,
    actionable_direction      TEXT,
    actionable                BOOLEAN NOT NULL,
    verdict                   TEXT NOT NULL,
    wait_ready_state          TEXT NOT NULL CHECK (wait_ready_state IN ('WAIT', 'READY')),
    blocked                   BOOLEAN NOT NULL DEFAULT FALSE,
    score                     NUMERIC,
    grade                     TEXT,
    confidence                NUMERIC,
    blockers                  JSONB NOT NULL DEFAULT '[]'::jsonb,
    waiting_for               JSONB NOT NULL DEFAULT '[]'::jsonb,
    waiting_for_guidance      TEXT,
    vwap_value                NUMERIC,
    vwap_status               TEXT,
    vwap_side                 TEXT,
    vwap_wording              TEXT,
    structure_cycle_state     TEXT,
    structure_next_event      TEXT,
    structure_context         JSONB NOT NULL DEFAULT '{}'::jsonb,
    freshness                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    databento_health          JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlations              JSONB NOT NULL DEFAULT '{}'::jsonb,
    safety_snapshot           JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_timestamp          TIMESTAMPTZ,
    source_module             TEXT NOT NULL DEFAULT 'full_analysis',
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload                   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_avh_scope_time
    ON authoritative_verdict_history (instrument, mode, recorded_at, event_id);

CREATE OR REPLACE FUNCTION reject_authoritative_verdict_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'authoritative_verdict_history is append-only';
END;
$$;

DROP TRIGGER IF EXISTS authoritative_verdict_history_immutable
    ON authoritative_verdict_history;
CREATE TRIGGER authoritative_verdict_history_immutable
    BEFORE UPDATE OR DELETE ON authoritative_verdict_history
    FOR EACH ROW
    EXECUTE FUNCTION reject_authoritative_verdict_history_mutation();

COMMENT ON TABLE authoritative_verdict_history IS
    'P4 observer-only immutable final verdict history; never a trading input.';