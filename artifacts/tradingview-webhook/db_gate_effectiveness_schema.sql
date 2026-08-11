-- Gate Effectiveness Audit — Phase 8C
-- Records every meaningful gate decision (ALLOWED + BLOCKED) with full
-- component breakdown and forward outcome tracking for counterfactuals.
--
-- Apply via Replit DB tool or publish schema-diff.
-- NEVER run DDL from app.py (app-side rule: INSERT/SELECT only).
-- DISPLAY/MEASUREMENT ONLY — never touches gate, execution, or risk.

CREATE TABLE IF NOT EXISTS gate_audit_log (
    -- Identity
    audit_id         TEXT        PRIMARY KEY,   -- deterministic dedup key (see gate_effectiveness.py)
    baseline_version TEXT        NOT NULL DEFAULT 'GATE_BASELINE_2026_08_11',
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at     TIMESTAMPTZ,               -- updated on every observation within the bucket

    -- Trade context
    instrument       TEXT        NOT NULL,
    direction        TEXT        NOT NULL,      -- 'Long' | 'Short'
    mode             TEXT        NOT NULL,      -- 'SCALP' | 'SWING'
    signal_time      TIMESTAMPTZ,

    -- Gate verdict
    edge_score       INTEGER,
    grade            TEXT,                      -- 'A+' | 'A' | 'B' | 'WAIT'
    gate_verdict     TEXT        NOT NULL,      -- 'ALLOWED' | 'EARLY_ALLOWED' | 'BLOCKED'
    full_verdict     TEXT        NOT NULL,      -- raw verdict string from full_analysis

    -- Blocking attribution
    primary_blocker  TEXT,                      -- first element of failed_conditions
    all_blockers     JSONB,                     -- complete failed_conditions list

    -- Proposed geometry (from trade_plan; NULL for pure WAIT with no plan)
    entry_price      NUMERIC,
    stop_price       NUMERIC,
    target1_price    NUMERIC,
    target2_price    NUMERIC,
    risk_points      NUMERIC,

    -- Gate component states: 'PASS' | 'FAIL' | 'UNAVAILABLE' | 'NOT_APPLICABLE'
    comp_bos         TEXT,
    comp_choch       TEXT,
    comp_vwap        TEXT,
    comp_sweep       TEXT,
    comp_volume      TEXT,
    comp_cvd         TEXT,
    comp_session     TEXT,
    comp_zone        TEXT,

    -- Market context at signal time
    atr_pts          NUMERIC,
    vwap_value       NUMERIC,
    cvd_direction    TEXT,
    trend_alignment  TEXT,
    volatility_regime TEXT,
    session          TEXT,

    -- Forward outcome (filled by counterfactual watcher for BLOCKED;
    --                   linked to strategy_trades for ALLOWED)
    outcome_status   TEXT        NOT NULL DEFAULT 'PENDING',  -- 'PENDING' | 'COMPLETED' | 'EXPIRED' | 'NO_GEOMETRY' | 'INSUFFICIENT_COUNTERFACTUAL_DATA'
    mfe_r            NUMERIC,
    mae_r            NUMERIC,
    mfe_price        NUMERIC,
    mae_price        NUMERIC,
    tp1_hit          BOOLEAN,
    tp2_hit          BOOLEAN,
    stop_hit         BOOLEAN,
    final_r          NUMERIC,
    bars_held        INTEGER,
    outcome_resolved_at TIMESTAMPTZ
);

-- Useful indexes for analytics queries
CREATE INDEX IF NOT EXISTS idx_gal_instrument   ON gate_audit_log (instrument);
CREATE INDEX IF NOT EXISTS idx_gal_verdict      ON gate_audit_log (gate_verdict);
CREATE INDEX IF NOT EXISTS idx_gal_outcome      ON gate_audit_log (outcome_status);
CREATE INDEX IF NOT EXISTS idx_gal_recorded_at  ON gate_audit_log (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_gal_edge_score   ON gate_audit_log (edge_score);
CREATE INDEX IF NOT EXISTS idx_gal_blockers     ON gate_audit_log USING gin (all_blockers);
