-- Native Journal schema
-- Provisioned out-of-band: database tool (dev) + Publish schema-diff (prod).
-- App code NEVER runs DDL.

CREATE TABLE IF NOT EXISTS native_journal (
    -- identity
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    internal_trade_id       UUID        UNIQUE,          -- FK → internal_trade_snapshots.internal_trade_id
    signal_id               TEXT,
    execution_fingerprint   TEXT,
    broker_order_id         TEXT,
    traderspost_id          TEXT,

    -- lifecycle
    lifecycle_status        TEXT        NOT NULL DEFAULT 'SUBMITTED',
    source_label            TEXT        NOT NULL DEFAULT 'SYSTEM_AUTO',

    -- ── immutable planned context (copied once from snapshot at creation) ──
    instrument              TEXT        NOT NULL,
    contract                TEXT,
    mode                    TEXT,
    session                 TEXT,
    direction               TEXT,
    canonical_strategy_key  TEXT,
    strategy_display_name   TEXT,
    setup_name              TEXT,
    playbook                TEXT,
    thesis_direction        TEXT,
    thesis_strength         TEXT,
    thesis_alignment        TEXT,
    edge_score              NUMERIC,
    grade                   TEXT,
    readiness               TEXT,
    confirmations           JSONB,
    blockers                JSONB,
    opposing_structure      TEXT,
    risk_state              TEXT,
    planned_entry           NUMERIC,
    planned_stop            NUMERIC,
    planned_targets         JSONB,
    planned_risk            NUMERIC,
    planned_contracts       INTEGER,
    planned_rr              NUMERIC,
    planned_dollar_risk     NUMERIC,
    market_data_timestamp   TIMESTAMPTZ,
    decision_timestamp      TIMESTAMPTZ,

    -- ── execution record (enriched post-send; never overwrites planned context) ──
    -- {submission_time, ack_time, broker_order_id, traderspost_id,
    --  actual_qty, fill_prices[], avg_entry, commissions, fees, slippage,
    --  rejected_reason, timeout_status, retry_history[]}
    execution               JSONB,

    -- ── management timeline (append-only array of events) ──
    -- [{timestamp, event_type, old_value, new_value, source,
    --   reason, automated, operator_id}]
    management_events       JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- ── outcome (set when lifecycle = CLOSED) ──
    -- {actual_exit, gross_pnl, net_pnl, commissions, fees,
    --  realized_r, duration_seconds, mae, mfe,
    --  best_unrealized_r, worst_unrealized_r,
    --  exit_reason, followed_plan, automation_outcome,
    --  manual_override_impact}
    outcome                 JSONB,

    -- ── override comparison ──
    -- {what_system_planned, what_operator_changed, reason,
    --  outcome_after, hypothetical_outcome_if_plan_continued}
    override_comparison     JSONB,

    -- ── review ──
    review_status           TEXT        NOT NULL DEFAULT 'UNREVIEWED',
    review_notes            TEXT,
    review_data             JSONB,
    screenshots             JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- ── learning eligibility ──
    learning_eligible       BOOLEAN     NOT NULL DEFAULT FALSE,
    learning_blocked_reason TEXT,

    -- ── tradzella link (secondary enrichment; never overwrites planned context) ──
    tradezella_trade_id     INTEGER,
    tradzella_enrichment    JSONB,

    -- ── migration link to legacy journal_entries ──
    legacy_journal_key      TEXT,

    -- ── timestamps ──
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS nj_internal_trade_id  ON native_journal (internal_trade_id);
CREATE INDEX IF NOT EXISTS nj_instrument_status  ON native_journal (instrument, lifecycle_status);
CREATE INDEX IF NOT EXISTS nj_created_at         ON native_journal (created_at DESC);
CREATE INDEX IF NOT EXISTS nj_broker_order_id    ON native_journal (broker_order_id) WHERE broker_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS nj_source_label       ON native_journal (source_label);
CREATE INDEX IF NOT EXISTS nj_review_status      ON native_journal (review_status);
