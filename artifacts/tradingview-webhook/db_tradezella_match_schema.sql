-- db_tradezella_match_schema.sql
-- Idempotent schema additions to tradezella_trades for Task #83:
-- TradeZella → Internal Snapshot matching, merge, review queue,
-- and learning-eligibility attribution.
--
-- Convention: no DDL in app code. Apply once per environment.
-- Apply dev:   psql $DATABASE_URL -f db_tradezella_match_schema.sql
-- Apply prod:  Replit Publish → "Schema diff" or paste into DB tool.
-- ─────────────────────────────────────────────────────────────────────────

-- ── Match result columns ──────────────────────────────────────────────────
-- Written by the matching engine at import time (and again by /rematch).
-- Read-only after the first write unless /rematch or manual assignment fires.

ALTER TABLE tradezella_trades
  ADD COLUMN IF NOT EXISTS matched_snapshot_id  UUID,              -- FK to internal_trade_snapshots.id
  ADD COLUMN IF NOT EXISTS match_method         TEXT,              -- broker_order_id | fingerprint | time+qty | time+price | none
  ADD COLUMN IF NOT EXISTS match_confidence     TEXT,              -- MATCHED_EXACT | MATCHED_HIGH_CONFIDENCE | MATCHED_LOW_CONFIDENCE | AMBIGUOUS | UNMATCHED
  ADD COLUMN IF NOT EXISTS candidate_count      INTEGER DEFAULT 0, -- how many snapshots were evaluated
  ADD COLUMN IF NOT EXISTS match_notes          TEXT,              -- diagnostic string from matcher

-- ── Attribution columns ───────────────────────────────────────────────────
  ADD COLUMN IF NOT EXISTS strategy_source      TEXT,              -- SYSTEM | MANUAL | IMPORTED | UNMATCHED
  ADD COLUMN IF NOT EXISTS learning_status      TEXT,              -- ELIGIBLE | REVIEW_REQUIRED | INELIGIBLE
  ADD COLUMN IF NOT EXISTS is_external_manual   BOOLEAN DEFAULT FALSE,  -- no snapshot within ±30 min

-- ── Snapshot-derived mirror fields (write-once, never overwritten) ─────────
-- Populated when match_confidence ∈ {MATCHED_EXACT, MATCHED_HIGH_CONFIDENCE}
-- or when operator manually assigns via PATCH /tradezella/review-queue/<id>.
  ADD COLUMN IF NOT EXISTS snap_strategy_key    TEXT,
  ADD COLUMN IF NOT EXISTS snap_strategy        TEXT,
  ADD COLUMN IF NOT EXISTS snap_thesis_direction TEXT,
  ADD COLUMN IF NOT EXISTS snap_thesis_strength  TEXT,
  ADD COLUMN IF NOT EXISTS snap_thesis_alignment TEXT,
  ADD COLUMN IF NOT EXISTS snap_edge_score      NUMERIC,
  ADD COLUMN IF NOT EXISTS snap_grade           TEXT,
  ADD COLUMN IF NOT EXISTS snap_planned_entry   NUMERIC,
  ADD COLUMN IF NOT EXISTS snap_planned_stop    NUMERIC,
  ADD COLUMN IF NOT EXISTS snap_planned_risk    NUMERIC,
  ADD COLUMN IF NOT EXISTS snap_planned_targets JSONB;

-- ── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tt_match_confidence ON tradezella_trades(match_confidence);
CREATE INDEX IF NOT EXISTS idx_tt_matched_snapshot  ON tradezella_trades(matched_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_tt_entry_time        ON tradezella_trades(entry_time);
