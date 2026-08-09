---
name: Profitability Engine Phase 1
description: Ghost observation pipeline — setup → immutable plan → market outcome → net R → edge ledger. Research/display-only, never touches gate or execution.
---

## Architecture

**New module:** `profitability_engine.py` — pure computation functions (no app.py imports, no side effects). Import in app.py via `import profitability_engine as _pe`.

**New table:** `ghost_observations` — created via DB tool (no DDL in app.py). Follows the `micro_scalp_ghost_trades` pattern exactly.

**Ghost creation point:** `_databento_bar_scan._scan()` — BEFORE `_maybe_auto_execute`, so every READY signal is captured regardless of arm/execution state.

**Watcher:** `_ghost_obs_bar_close()` registered as DatabentoBrain bar-close callback. Calls `_ghost_obs_watcher_cycle()` on each 1m bar close.

## Key functions

- `build_obs_key(instrument, direction, strategy_short, et_day, entry_bucket)` — stable dedup key
- `entry_bucket_from_price(price)` → nearest 0.5-pt bucket (boundary at ±0.25)
- `compute_commission_r(instrument, entry, stop, INSTRUMENT_SPECS, ...)` — uses SIM_REALISM_COMMISSION_PER_SIDE + SIM_REALISM_SLIPPAGE_TICKS
- `resolve_bar_outcome(...)` — stop-first on ambiguity (never optimistic)
- `aggregate_by_strategy_instrument(closed_rows)` — groups by (strategy_key × instrument)

## API endpoints
- `GET /profitability/summary` — edge ledger stats per strategy × instrument
- `GET /profitability/observations` — paginated raw observations

## Safety invariants
- `profitability_engine.py` imports NO app.py symbols
- Ghost creation is FAIL-OPEN (debug-log only, never raises)
- ghost_observations table is never read by any gate/scoring/execution code
- ON CONFLICT DO NOTHING ensures idempotency
- GHOST_OBS_DB_READY flag gates all DB writes (same pattern as micro ghost)

**Why:** Production needs the ghost_observations table applied via re-Publish before data flows in deployed instance.

## Two-leg exit tracking (follow-up #142 — complete)

DB columns added to ghost_observations: `tp1_hit`, `tp1_exit_price`, `tp1_gross_r`, `exit_model`.

**CLOSE_TP1_PARTIAL** sentinel: when `not tp1_hit and tp1_touched and target2 is not None`, `resolve_bar_outcome()` returns `(None, CLOSE_TP1_PARTIAL, tp1_exit_px, tp1_gross_r)` — status=None keeps the observation open. Watcher sets `tp1_hit=TRUE` and exit_model='two_leg_scalp'.

**compute_two_leg_gross_r(tp1_r, leg2_r, w1=0.5, w2=0.5)** — weighted average for 50/50 SCALP exit.

When a two-leg obs closes (stop/TP2/expiry after TP1): `gross_r = compute_two_leg_gross_r(tp1_gross_r, leg2_gross_r)`.

Single-leg observations (target2=None) — byte-identical to Phase 1 behavior.

## Edge Ledger dashboard panel (follow-up #141 — complete)

Panel `#mod-edge-ledger` in the Research view (`#view-research`). `elLoad()` fetches `/profitability/summary`, renders color-coded strategy × instrument table. Auto-loads when Research tab is opened. Color key: green (>0.1R), amber (0–0.1R), red (<0). Shows "accumulating…" until ≥10 closed trades.

## Known limitations
1. TV webhook path not wired (only databento_bar_scan fires the ghost — fine at 1m resolution)
2. No backfill of historical signals
3. cost_r is estimated (modelled), not actual broker fill
4. ghost_observations new columns need re-Publish for production schema (also applies to 4 new columns from #142)

## Tests
- 69 tests in `tests/test_profitability_phase1.py` — all pass
- 26 tests in `tests/test_profitability_two_leg.py` — all pass (95 total)
- Covers: constants, TP1-partial return, runner close, TP2 after TP1, ambiguity, weighted R, end-to-end cost, single-leg isolation
