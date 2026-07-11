---
name: Learning namespace isolation
description: Per-mode (SWING/SCALP/MICRO_SCALP) isolation for all learning caches — weights, eligibility, setup-disable rules.
---

## Rule
Every learning cache key is mode-namespaced so Swing stats never contaminate Scalp stats, and Scalp never contaminates Micro Scalp.

## Key helper
`_ns_learning_key(base_key, mode)` → `"{mode}::{base_key}"` when mode ∈ _VALID_LEARNING_MODES; bare key when unknown (warm-up fallback during first recompute cycle). Always try the namespaced key first, fall back to bare key.

## Cache layouts after this change
- `STRATEGY_WEIGHTS` / `LEARNING_SAMPLE_BY_KEY`: keyed as `"{mode}::{strategy_key}"` (e.g. `"SCALP::MGC_Long_CHOCH"`).
- `LEARNING_ELIGIBILITY`: keyed as `"{inst}::{mode}"` (e.g. `"MGC::SCALP"`). `"__today_labels__"` special key is unchanged.
- `disabled_setups` inside an eligibility entry: found under `LEARNING_ELIGIBILITY.get("{inst}::{mode}")`.
- `PER_MODE_STATS`: keyed by plain mode string (e.g. `"SWING"`) → `{n, win_rate, avg_r, expectancy, top_setup}`.

## DB columns stamped per trade
`strategy_trades` has 3 new columns (added via ALTER TABLE before this work):
- `setup_type` TEXT — strategy key with symbol+direction stripped
- `trigger_type` TEXT — last part of strategy key
- `learning_ns` TEXT — full 8-field composite key (`_make_learning_ns(…)`)

## Recompute SQL pattern
All three queries in `_recompute_learning` and `_recompute_learning_eligibility` include `COALESCE(trading_mode, 'SWING') AS trading_mode` in SELECT and GROUP BY. The eligibility loop iterates `(instrument × mode)` triples.

## Why
Without namespace isolation, a profitable SWING strategy boosts weights for an unrelated SCALP strategy that shares the same `strategy_key` prefix, creating phantom edge signals and false eligibility grants.

## How to apply
- Any new learning cache that holds per-strategy or per-instrument data MUST be namespaced.
- Call `_check_learning_eligibility(instrument, mode=TRADING_MODE)` — never the bare-instrument form in money-path code.
- Call `_strategy_weight_for(key, mode=TRADING_MODE)` — mode kwarg always.
- Goldens run with DB off → fail-open (neutral 1.0) → byte-identical. Namespace bugs only surface with real DB data.
