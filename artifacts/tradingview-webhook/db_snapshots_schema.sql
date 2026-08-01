-- db_snapshots_schema.sql
-- Idempotent schema for internal_trade_snapshots.
--
-- Apply once against the target database (dev or prod) before the first
-- bot restart that should record send-time snapshots.  All statements use
-- IF NOT EXISTS / IF NOT EXISTS so re-running is always safe.
--
-- Convention: no DDL in app code (_boot_snapshots_table does a readiness
-- probe only).  This file is the authoritative schema artifact for the
-- internal_trade_snapshots table.
--
-- Apply dev:   psql $DATABASE_URL -f db_snapshots_schema.sql
-- Apply prod:  run via Replit Publish → "Schema diff" or copy/paste into
--              the production database tool.
-- ─────────────────────────────────────────────────────────────────────────

-- Immutable send-time context snapshot.
-- One row per confirmed broker send (live 2xx), paper-simulate, or two-leg
-- runner primary confirmation.  Never updated after INSERT; matching/merge
-- logic in Task #83 links rows to TradeZella imports via execution_fingerprint.
CREATE TABLE IF NOT EXISTS internal_trade_snapshots (
    -- surrogate PK (UUID avoids serial sequence contention on bulk imports)
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- stable identifier built by trade_snapshot.build_trade_snapshot()
    -- UNIQUE constraint prevents duplicate captures for the same logical send event
    internal_trade_id      UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,

    -- optional upstream signal identifier (e.g. from a webhook payload)
    signal_id              TEXT,

    -- deterministic fingerprint: sha256(instrument|direction|entry_2dp|contracts|second)[:32]
    -- used by Task #83 to match TradeZella rows back to this snapshot
    execution_fingerprint  TEXT,

    -- ── Instrument / account ─────────────────────────────────────────────
    instrument             TEXT NOT NULL,        -- canonical registry key (e.g. MNQ)
    contract               TEXT,                 -- broker symbol (e.g. MNQH26)
    account                TEXT,                 -- broker account ID if available

    -- ── Execution parameters ─────────────────────────────────────────────
    mode                   TEXT,                 -- traderspost | paper | manual_only
    direction              TEXT,                 -- Long | Short
    source                 TEXT,                 -- auto | manual | paper | manual_desk

    -- ── Strategy identity at send time ───────────────────────────────────
    canonical_strategy_key TEXT,                 -- e.g. CHOCH_DEMAND_PULLBACK
    strategy_display_name  TEXT,                 -- e.g. "CHoCH Demand Pullback"
    setup_name             TEXT,                 -- e.g. "Demand Zone Pullback"
    playbook               TEXT,                 -- free-text playbook note from Main Brain

    -- ── Thesis / market context ──────────────────────────────────────────
    thesis_direction       TEXT,                 -- Long | Short | Neutral
    thesis_strength        TEXT,                 -- HIGH | MEDIUM | LOW
    thesis_alignment       TEXT,                 -- ALIGNED | OPPOSED | NEUTRAL

    -- ── Edge quality ─────────────────────────────────────────────────────
    edge_score             NUMERIC,              -- 0-110 edge score at send time
    grade                  TEXT,                 -- A+ | A | B | WAIT
    readiness              TEXT,                 -- full verdict string (e.g. "Long READY")
    actionable             BOOLEAN,              -- true iff "READY" in verdict

    -- ── Gate context ─────────────────────────────────────────────────────
    confirmations          JSONB,                -- list of confirmed signals
    blockers               JSONB,                -- list of active blockers ([] = none)
    opposing_structure     TEXT,                 -- description of opposing BOS/CHOCH if any
    risk_state             TEXT,                 -- volatility / risk regime string

    -- ── Planned trade geometry ───────────────────────────────────────────
    planned_entry          NUMERIC,              -- entry price from trade plan
    planned_stop           NUMERIC,              -- stop-loss price
    planned_targets        JSONB,                -- {"t1": …, "t2": …}
    planned_risk           NUMERIC,              -- |entry - stop| in price points
    planned_contracts      INTEGER,              -- total contracts sent (all legs)

    -- ── Broker response metadata ─────────────────────────────────────────
    broker_order_id        TEXT,                 -- TradersPost orderId / order_id / id
    broker_signal_id       TEXT,                 -- TradersPost signalId / signal_id
    broker_metadata        JSONB,                -- full parsed broker response + runner meta

    -- ── Timestamps ───────────────────────────────────────────────────────
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at                TIMESTAMPTZ           -- when the broker request was issued
);

-- ── Indexes ───────────────────────────────────────────────────────────────

-- Most common read: "show me all snapshots for instrument X"
CREATE INDEX IF NOT EXISTS idx_its_instrument
    ON internal_trade_snapshots(instrument);

-- Time-range scans and newest-first ordering for the debug endpoint
CREATE INDEX IF NOT EXISTS idx_its_created_at
    ON internal_trade_snapshots(created_at DESC);

-- Task #83 matching: join on fingerprint to locate a TradeZella row's snapshot
CREATE INDEX IF NOT EXISTS idx_its_fingerprint
    ON internal_trade_snapshots(execution_fingerprint);
