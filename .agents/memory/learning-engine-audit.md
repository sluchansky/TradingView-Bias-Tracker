---
name: Learning Engine Audit (Phase 7I)
description: Confirmed defects, pipeline trace, and diagnostic layer for the adaptive learning engine. No logic changed — display and diagnostics only.
---

## Pipeline Trace (verified)

thesis created → thesis resolved (_THESIS_LAST_RESOLVED_AT set)
→ _record_strategy_trade called at trade close (managed_key UNIQUE + ON CONFLICT DO NOTHING)
→ strategy_trades table (6 rows in prod as of 2026-07-31)
→ _maybe_recompute_learning → _learning_weight(n<20) → 1.0 neutral
→ STRATEGY_WEIGHTS["{mode}::{strategy_key}"] in memory + DB strategy_weights (bare key)
→ _strategy_weight_for(key, mode): ns_key tried, bare key fallback → 1.0 if missing
→ _resolve_learning_score_influence: returns None when sample < LEARNING_MIN_SAMPLE
→ result["learning_score_influence"] = None → delta = 0
→ build_coach_interface: learning_influence=0.0, learning_diagnostics.blocked_reason

## Confirmed Production Defects (2026-07-31)

**DEFECT 1 (IMMEDIATE): INSUFFICIENT_SAMPLES**
- LEARNING_MIN_SAMPLE = 20; prod has 5 (MGC) and 1 (MNQ)
- _resolve_learning_score_influence returns None → delta = 0
- This is intended behavior; just needs 20 closed trades to activate

**DEFECT 2 (LATENT): win_rate SQL case mismatch**
- strategy_trades.result stores 'WIN' (uppercase)
- SQL: avg((result='Win')::int) uses mixed case → 0 for all rows
- win_rate = 0.0 even when 100% winning — when n hits 20, weights will penalize winners
- Affects both _recompute_learning (line ~12579) and _recompute_learning_eligibility (line 12313)
- Fix: change result='Win' to lower(result)='win' in both SQL queries

**DEFECT 3 (UI): "Weight Updated: YES" was misleading**
- weight_updated = bool(LEARNING_ANALYTICS["updated_at"]) = True when recompute ran
- With n<20: stored weight=1.0 (neutral, no-op). "YES" ≠ numeric change
- Fixed in Phase 7I: replaced with weight_status (INSUFFICIENT_SAMPLES / NO_CHANGE / UPDATED / DISABLED)

**DEFECT 4 (DISPLAY SOURCE MISMATCH): Performance Sample = 0 is correct**
- performance.sample = _main_brain_review_snapshot()["decided_trades"] from main_brain_events
- NOT from strategy_trades (6 rows). Two separate datasets, intentionally isolated.
- Prod shows 0 decided_trades in main_brain_events — this is correct for that source.

## Key Format Finding (Case G)

- _resolve_learning_score_influence uses compute_strategy_engine().active_key (e.g. "CHOCH_LONG")
- strategy_trades stores ctx.get("strategy_key") captured at entry (e.g. "MGC_SCALP_CHOCH_Long")
- If these differ in format, _strategy_weight_for lookup always misses → 1.0 neutral
- Currently masked by Defect 1 (n<20). Verify format alignment in production trade registrations.

## What Was Added (Phase 7I, no logic changes)

- build_coach_interface returns learning_diagnostics dict with:
  enabled, influence_enabled, mode, eligible, strategy_key, sample_count,
  minimum_samples, closed_trade_count, current_weight, weight_delta,
  influence_points, last_weight_update_at, applied_to_live_score, blocked_reason,
  weight_status (UPDATED|NO_CHANGE|INSUFFICIENT_SAMPLES|NOT_ELIGIBLE|KEY_NOT_FOUND|DISABLED), source

- CoachPanel redesigned: status chip + sample progress bar + weight grid + blocked-reason panel

- test_learning_engine.py: 40 checks, Cases A-J

## Recommended Phase 2 Corrections (NOT implemented)

1. Fix win_rate SQL: lower(result)='win' in both recompute functions
2. Verify strategy_key format alignment (active_key vs ctx.strategy_key at trade registration)
3. Consider lowering LEARNING_MIN_SAMPLE from 20 to 10-15 given 6 prod trades
