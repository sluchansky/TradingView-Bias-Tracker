-- Phase 8B.1 — Multi-Timeframe Trend Alignment schema patch
-- Adds 3 trend-context columns to ghost_observations and edge_ledger.
-- Existing rows are left NULL (historical observations had no trend context).
-- These columns are research metadata only — never used in gate/scoring/execution.

-- ghost_observations: add trend context at signal time
ALTER TABLE ghost_observations
  ADD COLUMN IF NOT EXISTS four_h_trend_at_signal    TEXT,
  ADD COLUMN IF NOT EXISTS fifteen_m_trend_at_signal TEXT,
  ADD COLUMN IF NOT EXISTS trend_alignment_at_signal TEXT;

-- edge_ledger: add trend context at signal time
ALTER TABLE edge_ledger
  ADD COLUMN IF NOT EXISTS four_h_trend_at_signal    TEXT,
  ADD COLUMN IF NOT EXISTS fifteen_m_trend_at_signal TEXT,
  ADD COLUMN IF NOT EXISTS trend_alignment_at_signal TEXT;

-- Verify
SELECT
  'ghost_observations' AS tbl,
  column_name,
  data_type
FROM information_schema.columns
WHERE table_name = 'ghost_observations'
  AND column_name IN ('four_h_trend_at_signal','fifteen_m_trend_at_signal','trend_alignment_at_signal')
UNION ALL
SELECT
  'edge_ledger' AS tbl,
  column_name,
  data_type
FROM information_schema.columns
WHERE table_name = 'edge_ledger'
  AND column_name IN ('four_h_trend_at_signal','fifteen_m_trend_at_signal','trend_alignment_at_signal')
ORDER BY tbl, column_name;
