-- Order Flow V1 Schema Patch — ghost_observations table extension
-- Adds nullable order-flow observation columns to ghost_observations.
-- All columns are nullable: pre-V1 rows stay NULL; new rows get values
-- from order_flow_engine.compute_order_flow() at signal time.
--
-- Apply each ALTER TABLE statement individually via Replit DB tool.
-- NEVER run DDL from app.py (app-side rule: INSERT/SELECT only).
-- DISPLAY/MEASUREMENT ONLY — never touches gate, execution, or risk.

-- Per-bar metrics
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_bar_delta         BIGINT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_delta_ratio        NUMERIC(8,4);
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_delta_acceleration  BIGINT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_ask_volume          BIGINT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_bid_volume          BIGINT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_book_imbalance      NUMERIC(8,4);

-- Session / series metrics
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_cvd                NUMERIC(14,2);
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_cvd_slope           NUMERIC(14,2);
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_cvd_divergence      TEXT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_absorption_side      TEXT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_absorption_strength  TEXT;

-- Composite output
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_order_flow_score    SMALLINT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_order_flow_state    TEXT;
ALTER TABLE ghost_observations ADD COLUMN IF NOT EXISTS of_reversal_confirmed  BOOLEAN;

-- Research indexes for analytics queries
CREATE INDEX IF NOT EXISTS idx_ghost_of_score
    ON ghost_observations (of_order_flow_score)
    WHERE of_order_flow_score IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ghost_of_state
    ON ghost_observations (of_order_flow_state)
    WHERE of_order_flow_state IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ghost_of_reversal
    ON ghost_observations (of_reversal_confirmed)
    WHERE of_reversal_confirmed IS NOT NULL;
