---
name: Real Learning Rule Engine
description: Per-instrument ghost/live eligibility gate in execute_trade_gateway, backed by strategy_trades DB evidence
---

## Rule

`_check_learning_eligibility(instrument)` returns `(status, rule_reason)` from the in-memory
`LEARNING_ELIGIBILITY` cache. In `execute_trade_gateway`:
- `GHOST_ONLY` + live mode → `mode = "paper"` (demote-only; trade still fires, builds evidence)
- `DISABLED` → 409 block (reserved for repeatedly-failing setups, n>=15 AND WR<25% AND Exp<-0.5)
- No data / DB off → FAIL-OPEN (pass through unchanged)

## Rules for GHOST_ONLY

- `n < LEARNING_LIVE_MIN_SAMPLE` (default 50) → GHOST_ONLY
- `expectancy < 0` → GHOST_ONLY
- `last_20_avg_r < 0` (last 20 trades trending negative) → GHOST_ONLY
- All positive → LIVE_ELIGIBLE

## Key Invariants

**Why:** User wanted "persistent closed-trade learning that changes future trade eligibility" — not just display panels.

**How to apply:** 
- Gate fires AFTER all source-specific checks (dual_tf, micro_scalp, etc.) and BEFORE the risk cap.
- E1 constants MUST be in globals (LEARNING_ELIGIBILITY, LEARNING_ELIGIBILITY_LOCK, LEARNING_LIVE_MIN_SAMPLE, LEARNING_SETUP_DISABLE_*).
- `_recompute_learning_eligibility(conn)` is called inside `_recompute_learning()` (same connection, same lock) right before the LEARNING_ANALYTICS swap.
- Boot fires eligibility recompute: "learning_eligibility recomputed: 4 instruments" in logs = working.
- DB empty at launch → all instruments GHOST_ONLY by default (correct behavior — bot earns live trading through evidence).
- E1 was initially never written (first script had `AssertionError` before `file.write()`); always verify constants exist before assuming they're in scope.

## DB Tables

- `strategy_trades.trade_label` (11 labels: WIN/LOSS/BREAKEVEN/TP1_THEN_BE/LATE_ENTRY/EARLY_ENTRY/STOPPED_BEFORE_MOVE/STOP_TOO_TIGHT/NO_FOLLOW_THROUGH/BAD_SESSION/BAD_SETUP)
- `learning_eligibility` (per-instrument status, sample_size, expectancy, win_rate, etc.)
- `learning_setup_rules` (per-instrument+setup_key disabled tracking)

## Dashboard

Panel `mod-rule-engine` (always visible, not mb-hidden). `renderRuleEngine(d)` reads `d.learning_rule_engine`
from `/status`. `_learning_rule_engine_view()` reads LEARNING_ELIGIBILITY cache.
