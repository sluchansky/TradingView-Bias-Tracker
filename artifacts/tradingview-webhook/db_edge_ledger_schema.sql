-- db_edge_ledger_schema.sql
-- Phase 8A: Immutable Edge Ledger — signal-vs-management accounting.
--
-- Apply once against the target database (dev or prod) before the first
-- bot restart that should record edge-ledger entries. All statements use
-- IF NOT EXISTS so re-running is always safe.
--
-- INVARIANT: original signal fields (original_entry, original_stop, original_tp1,
--            original_tp2, original_risk_points, original_risk_dollars,
--            original_contracts, original_rr, edge_score, long_score,
--            short_score, grade, readiness, thesis_alignment, left_brain_thesis,
--            confirmations, blockers, opposing_structure, risk_state,
--            market_context) are written ONCE and NEVER UPDATED.
--
-- Convention: app code NEVER runs DDL.
-- Apply dev:   psql $DATABASE_URL -f db_edge_ledger_schema.sql
-- Apply prod:  Replit Publish → "Schema diff" or copy/paste into prod DB tool.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS edge_ledger (

    -- ── Identity ──────────────────────────────────────────────────────────────
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_id                 TEXT        NOT NULL UNIQUE,   -- stable dedup key (same format as obs_key)
    internal_trade_id       UUID,                          -- FK → native_journal.internal_trade_id (nullable until trade fires)
    ghost_obs_key           TEXT,                          -- FK → ghost_observations.obs_key (same dedup key)
    signal_id               TEXT,                          -- upstream signal identifier if present
    execution_fingerprint   TEXT,                          -- sha256 fingerprint from internal_trade_snapshots

    -- ── Classification ────────────────────────────────────────────────────────
    source                  TEXT        NOT NULL DEFAULT 'live_shadow',  -- live_shadow | paper | backtest
    instrument              TEXT        NOT NULL,
    contract                TEXT,
    mode                    TEXT,
    session                 TEXT,
    direction               TEXT,
    strategy_key            TEXT,
    strategy_version        TEXT,
    config_version          TEXT,
    sample_partition        TEXT        NOT NULL DEFAULT 'UNKNOWN',
    -- DEVELOPMENT | FORWARD_VALIDATION | SHADOW | PAPER | LIVE | HISTORICAL | UNKNOWN

    -- ── ORIGINAL SIGNAL — IMMUTABLE ──────────────────────────────────────────
    -- Written once at ghost_observe_setup time. NEVER updated by any later path.
    signal_timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_data_timestamp   TIMESTAMPTZ,

    original_entry          NUMERIC     NOT NULL,
    original_stop           NUMERIC     NOT NULL,
    original_tp1            NUMERIC     NOT NULL,
    original_tp2            NUMERIC,
    original_targets        JSONB,                -- {"t1":…,"t2":…} — snapshot of all targets
    original_risk_points    NUMERIC     NOT NULL,
    original_risk_dollars   NUMERIC,              -- original_risk_points × point_value (estimated)
    original_contracts      INTEGER,
    original_rr             NUMERIC,              -- |tp1 − entry| / |entry − stop|

    edge_score              NUMERIC,              -- 0-110 edge score at signal time
    long_score              NUMERIC,              -- directional long score
    short_score             NUMERIC,              -- directional short score
    decision_margin         NUMERIC,              -- |long_score − short_score|
    grade                   TEXT,                 -- A+ | A | B | WAIT

    left_brain_thesis       TEXT,                 -- thesis direction at signal time
    thesis_alignment        TEXT,                 -- ALIGNED | OPPOSED | NEUTRAL
    readiness               TEXT,                 -- full verdict string

    confirmations           JSONB,                -- list of confirmed signals
    blockers                JSONB,                -- list of active blockers
    opposing_structure      TEXT,                 -- opposing BOS/CHOCH description
    risk_state              TEXT,                 -- volatility/risk regime
    market_context          JSONB,                -- atr, cvd_dir, vwap_side, regime, session

    -- ── SIGNAL OUTCOME ────────────────────────────────────────────────────────
    -- Determined by bar-by-bar market data using ORIGINAL frozen terms only.
    -- Never uses mutated stop/target values.
    signal_outcome_status   TEXT,                 -- open | closed | expired
    signal_first_level_hit  TEXT,                 -- stop | tp1 | tp2 | expired | ambiguous
    signal_stop_hit_at      TIMESTAMPTZ,
    signal_tp1_hit_at       TIMESTAMPTZ,
    signal_tp2_hit_at       TIMESTAMPTZ,

    signal_mfe_points       NUMERIC,              -- max favorable excursion in price points
    signal_mae_points       NUMERIC,              -- max adverse excursion in price points
    signal_mfe_r            NUMERIC,              -- MFE in R-multiples
    signal_mae_r            NUMERIC,              -- MAE in R-multiples
    signal_gross_r          NUMERIC,              -- raw R using original terms
    signal_net_r            NUMERIC,              -- signal_gross_r − signal_commission_estimate

    signal_gross_pnl        NUMERIC,              -- estimated $ P&L (gross, pre-cost)
    signal_net_pnl          NUMERIC,              -- estimated $ P&L (net, post-cost)

    signal_commission_estimate  NUMERIC,          -- round-trip commission in $
    signal_fee_estimate         NUMERIC,          -- exchange fees in $ (if applicable)
    signal_slippage_estimate    NUMERIC,          -- slippage in $
    signal_cost_r               NUMERIC,          -- total cost in R-multiples (ESTIMATED)
    cost_model_version          TEXT DEFAULT 'v1',-- which cost-model constants were used

    signal_resolved_at      TIMESTAMPTZ,          -- when signal outcome was finalized

    -- ── MANAGED OUTCOME ───────────────────────────────────────────────────────
    -- Populated from native_journal when a live/paper trade actually executes.
    -- Reflects what ACTUALLY happened to the position after management.
    managed_outcome_status  TEXT,                 -- SUBMITTED | CLOSED | REJECTED | CANCELED
    actual_entry            NUMERIC,              -- actual fill price (from native_journal)
    actual_exit             NUMERIC,              -- actual exit price
    actual_quantity         INTEGER,              -- actual contracts filled

    managed_gross_r         NUMERIC,              -- gross R based on actual entry/exit/planned_stop
    managed_net_r           NUMERIC,              -- net R after actual costs
    managed_gross_pnl       NUMERIC,              -- actual gross P&L in $
    managed_net_pnl         NUMERIC,              -- actual net P&L in $

    actual_commissions      NUMERIC,              -- actual commission paid (from broker/journal)
    actual_fees             NUMERIC,              -- actual exchange fees paid
    actual_slippage         NUMERIC,              -- actual slippage vs. planned entry

    managed_resolved_at     TIMESTAMPTZ,          -- when managed outcome was finalized

    -- ── COMPARISON ────────────────────────────────────────────────────────────
    -- Computed when both signal_net_r and managed_net_r are available.
    signal_vs_managed_delta_r   NUMERIC,          -- managed_net_r − signal_net_r
    signal_vs_managed_delta_pnl NUMERIC,          -- managed_net_pnl − signal_net_pnl
    management_helped           BOOLEAN,          -- TRUE=helped, FALSE=hurt, NULL=neutral/unknown
    comparison_complete         BOOLEAN NOT NULL DEFAULT FALSE,
    comparison_reason           TEXT,             -- MANAGEMENT_HELPED|MANAGEMENT_HURT|MANAGEMENT_NEUTRAL|COMPARISON_UNAVAILABLE

    -- ── VALIDATION ────────────────────────────────────────────────────────────
    eligibility_state       TEXT,                 -- mirrors learning_eligibility state
    data_complete           BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_reason          TEXT,                 -- why data_complete=FALSE if applicable
    edge_ledger_ready_for_learning  BOOLEAN NOT NULL DEFAULT FALSE,
    -- ^ Set to TRUE only when both signal+managed outcomes resolve and pass quality checks.
    -- Learning engine DOES NOT read this yet (Phase 8B). Display-only in Phase 8A.

    backfill_classification TEXT,                 -- SAFE_TO_BACKFILL | PARTIAL_BACKFILL | UNSAFE_TO_BACKFILL

    -- ── Timestamps ────────────────────────────────────────────────────────────
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Primary lookup: stable dedup key (prevents duplicate signal entries)
CREATE UNIQUE INDEX IF NOT EXISTS el_edge_id
    ON edge_ledger (edge_id);

-- Link to ghost_observations resolution
CREATE INDEX IF NOT EXISTS el_ghost_obs_key
    ON edge_ledger (ghost_obs_key) WHERE ghost_obs_key IS NOT NULL;

-- Link to native_journal managed outcome
CREATE INDEX IF NOT EXISTS el_internal_trade_id
    ON edge_ledger (internal_trade_id) WHERE internal_trade_id IS NOT NULL;

-- Common diagnostic query patterns
CREATE INDEX IF NOT EXISTS el_instrument_status
    ON edge_ledger (instrument, signal_outcome_status);

CREATE INDEX IF NOT EXISTS el_strategy_key
    ON edge_ledger (strategy_key);

CREATE INDEX IF NOT EXISTS el_sample_partition
    ON edge_ledger (sample_partition);

CREATE INDEX IF NOT EXISTS el_signal_timestamp
    ON edge_ledger (signal_timestamp DESC);

CREATE INDEX IF NOT EXISTS el_comparison_complete
    ON edge_ledger (comparison_complete) WHERE comparison_complete = TRUE;

-- ── Immutability trigger ───────────────────────────────────────────────────────
-- Prevent any UPDATE from modifying the original signal columns.
-- Raises an exception if any of the frozen columns change after INSERT.

CREATE OR REPLACE FUNCTION el_guard_original_signal_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (
        NEW.original_entry          IS DISTINCT FROM OLD.original_entry          OR
        NEW.original_stop           IS DISTINCT FROM OLD.original_stop           OR
        NEW.original_tp1            IS DISTINCT FROM OLD.original_tp1            OR
        NEW.original_tp2            IS DISTINCT FROM OLD.original_tp2            OR
        NEW.original_targets        IS DISTINCT FROM OLD.original_targets        OR
        NEW.original_risk_points    IS DISTINCT FROM OLD.original_risk_points    OR
        NEW.original_risk_dollars   IS DISTINCT FROM OLD.original_risk_dollars   OR
        NEW.original_contracts      IS DISTINCT FROM OLD.original_contracts      OR
        NEW.original_rr             IS DISTINCT FROM OLD.original_rr             OR
        NEW.edge_score              IS DISTINCT FROM OLD.edge_score              OR
        NEW.long_score              IS DISTINCT FROM OLD.long_score              OR
        NEW.short_score             IS DISTINCT FROM OLD.short_score             OR
        NEW.grade                   IS DISTINCT FROM OLD.grade                   OR
        NEW.readiness               IS DISTINCT FROM OLD.readiness               OR
        NEW.left_brain_thesis       IS DISTINCT FROM OLD.left_brain_thesis       OR
        NEW.thesis_alignment        IS DISTINCT FROM OLD.thesis_alignment        OR
        NEW.confirmations           IS DISTINCT FROM OLD.confirmations           OR
        NEW.blockers                IS DISTINCT FROM OLD.blockers                OR
        NEW.opposing_structure      IS DISTINCT FROM OLD.opposing_structure      OR
        NEW.risk_state              IS DISTINCT FROM OLD.risk_state              OR
        NEW.market_context          IS DISTINCT FROM OLD.market_context          OR
        NEW.signal_timestamp        IS DISTINCT FROM OLD.signal_timestamp        OR
        NEW.direction               IS DISTINCT FROM OLD.direction               OR
        NEW.instrument              IS DISTINCT FROM OLD.instrument              OR
        NEW.strategy_key            IS DISTINCT FROM OLD.strategy_key
    ) THEN
        RAISE EXCEPTION
            'edge_ledger: original signal fields are immutable after INSERT (edge_id=%)', OLD.edge_id;
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS el_immutability_guard ON edge_ledger;
CREATE TRIGGER el_immutability_guard
    BEFORE UPDATE ON edge_ledger
    FOR EACH ROW
    EXECUTE FUNCTION el_guard_original_signal_immutable();
