---
name: Learning Engine Audit (Phase 7I + 7I.1)
description: Confirmed defects, pipeline trace, diagnostic layer, canonical key system, and SQL win-rate fix for the adaptive learning engine.
---

## Pipeline Trace (verified)

thesis created → thesis resolved (_THESIS_LAST_RESOLVED_AT set)
→ _record_strategy_trade called at trade close (managed_key UNIQUE + ON CONFLICT DO NOTHING)
→ strategy_trades table (6 rows in prod as of 2026-07-31, all SCALP, result='WIN')
→ _maybe_recompute_learning → _learning_weight(n<20) → 1.0 neutral
→ STRATEGY_WEIGHTS["{mode}::{strategy_key}"] in memory + DB strategy_weights (bare key)
→ _strategy_weight_for(key, mode, instrument=None): (weight, sample, lookup_status) 3-tuple
   - CANONICAL: "{mode}::{key}" or bare key found directly
   - LEGACY_COMPAT: legacy "{inst}_{mode}_{type}_{dir}" parsed and mapped to canonical
   - NOT_FOUND: key missing, returns neutral (1.0, 0, "NOT_FOUND")
→ _resolve_learning_score_influence: returns None when sample < LEARNING_MIN_SAMPLE
→ result["learning_score_influence"] = None → delta = 0
→ build_coach_interface: learning_influence=0.0, learning_diagnostics.blocked_reason

## Canonical Key Format (Phase 7I.1)

Canonical key = one of the 5 STRATEGY_PRIORITY identifiers:
  OPENING_DRIVE, LIQUIDITY_SWEEP_REVERSAL, VWAP_TREND_CONTINUATION,
  RANGE_EXPANSION_BREAKOUT, OPENING_RANGE_BREAKOUT

Namespaced key in STRATEGY_WEIGHTS = "{mode}::{canonical_key}"

Legacy key format (older code): "{instrument}_{mode}_{strategy_type}_{direction}"
  e.g. "MGC_SCALP_CHOCH_Long" — stored in prod strategy_trades

_canonical_learning_key(raw_key) → (canonical_key, "CANONICAL"|"LEGACY_COMPAT"|"NOT_FOUND")
_LEGACY_STRATEGY_KEY_MAP: CHOCH→None, BOS→None (no current equivalents; both → NOT_FOUND)

**Why CHOCH legacy data is NOT applied**: strategy_type "CHOCH" has no deterministic mapping
to any current STRATEGY_PRIORITY key. Legacy weights are preserved in storage but never
applied to live lookups. This is the correct safe behavior.

Instrument guard in _strategy_weight_for: when instrument is provided, legacy keys with a
different stored instrument are skipped (MGC weight never applied to MNQ lookup).

## Confirmed Production Defects (2026-07-31)

**DEFECT 1 (IMMEDIATE): INSUFFICIENT_SAMPLES** — expected behavior, not a bug
- LEARNING_MIN_SAMPLE = 20; prod has n=5 (MGC) and n=1 (MNQ) closed trades
- _resolve_learning_score_influence returns None → delta = 0

**DEFECT 2 (LATENT → FIXED in Phase 7I.1): win_rate SQL case mismatch**
- strategy_trades.result stores 'WIN' (uppercase)
- SQL used: avg((result='Win')::int::float) → 0 for all rows
- Fixed: all 14 occurrences changed to lower(trim(result))='win' (and 'loss')
- Before fix at n=20: win_rate=0.0 → penalizing weight (weight<1.0) for 100% winners
- After fix at n=20: win_rate=1.0 → boosting weight (weight>1.0) — correct

**DEFECT 3 (UI → FIXED in Phase 7I)**: weight_updated boolean replaced with weight_status string

**DEFECT 4 (KEY MISMATCH → FIXED in Phase 7I.1)**
- Legacy prod rows have strategy_key = "MGC_SCALP_CHOCH_Long" (instrument-prefixed)
- Current engine's active_key = STRATEGY_PRIORITY key (bare, e.g. "LIQUIDITY_SWEEP_REVERSAL")
- _strategy_weight_for now tries: CANONICAL → LEGACY_COMPAT → NOT_FOUND
- CHOCH has no mapping → NOT_FOUND for all 6 prod rows → neutral 1.0 (safe)

## What Was Added (Phase 7I, no logic changes)

build_coach_interface returns learning_diagnostics dict including:
  enabled, influence_enabled, mode, eligible, strategy_key, sample_count,
  minimum_samples, closed_trade_count, current_weight, weight_delta,
  influence_points, last_weight_update_at, applied_to_live_score, blocked_reason,
  weight_status (UPDATED|NO_CHANGE|INSUFFICIENT_SAMPLES|NOT_ELIGIBLE|KEY_NOT_FOUND|DISABLED),
  source, canonical_strategy_key, stored_strategy_key, lookup_status,
  result_normalization_status ("FIXED"), aggregate_win_count, aggregate_loss_count,
  aggregate_win_rate

## What Was Added (Phase 7I.1)

- _canonical_learning_key(raw_key) → (canonical, status) helper
- _LEGACY_STRATEGY_KEY_MAP: explicit CHOCH/BOS→None mapping (no safe equivalent)
- _strategy_weight_for now returns 3-tuple (weight, sample, lookup_status)
- Instrument guard parameter added to _strategy_weight_for
- weight_status priority: KEY_NOT_FOUND fires before INSUFFICIENT_SAMPLES (guarded by _active_key)
- test_learning_key_compat.py: 56 checks, Cases A-O
- test_learning_engine.py: updated to 42 checks with 3-tuple unpack
- All 14 result='Win'/'Loss' SQL occurrences fixed to lower(trim(result))

## Before/After Production Aggregation

Before fix (all result='Win' rows → 0 by case-sensitive compare):
  MGC_SCALP_CHOCH_Long: n=5, win_rate=0.0 (bug), avg_r=1.5
  MNQ_SCALP_CHOCH_Long: n=1, win_rate=0.0 (bug), avg_r=1.5

After fix (next recompute will see):
  MGC_SCALP_CHOCH_Long: n=5, win_rate=1.0, avg_r=1.5
  MNQ_SCALP_CHOCH_Long: n=1, win_rate=1.0, avg_r=1.5
  Note: still masked by Defect 1 (n<20 → weight stays 1.0 neutral until more trades close)
