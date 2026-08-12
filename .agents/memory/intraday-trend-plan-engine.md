---
name: INTRADAY_TREND dedicated plan engine
description: build_intraday_trade_plan() is fully separated from SWING; key design decisions, test fixture pitfalls, and what _it_entry_veto_reasons reads from ctx.
---

## The dedicated builder

`build_intraday_trade_plan()` is called at the TOP of `build_strict_trade_plan()` via:
```python
if mode == "INTRADAY_TREND":
    return build_intraday_trade_plan(...)
```
All SCALP/SWING logic below that routing block is never reached for IT.

## _swing_htf_enabled() is SWING-only
`_swing_htf_enabled(m)` returns `m != "SWING"` — IT is **intentionally excluded**.
IT still gets `swing_ctx` computed (for `compute_intraday_trend_context`'s `daily_levels`), but via the
`if _swing_htf_enabled(TRADING_MODE) or TRADING_MODE == "INTRADAY_TREND":` line — never through the HTF path.

## Stop methodology
Stop comes EXCLUSIVELY from `it_ctx["structural_stop_level"]`/`["structural_stop_pts"]`.
ATR sanity bounds are applied (0.3×–4× ATR) but ATR is NEVER the stop source.
Code: `IT_STRUCTURE_FAIL` veto when stop_valid=False or bounds violated.

## Target selection
`_it_select_intraday_target()` scans merged session+daily levels; returns nearest level ≥ min_rr×risk away.
`min_rr` default 2.0 (env `MIN_INTRADAY_RR`). No qualifying level → `IT_INSUFFICIENT_RR`.
TP1 from `_it_find_tp1()`: nearest structural level in 0.75R–1.5R; fallback 1.25R×risk.

## Entry cutoff
Default 15:15 ET (`_IT_LAST_ENTRY_DEFAULT = "15:15"`).
Env priority: `INTRADAY_NEW_ENTRY_CUTOFF_ET` > `IT_LAST_NEW_ENTRY_TIME` > default.

## Chase gate
`chase_dist = abs(current_price - entry_anchor)`.
`entry_anchor` = `nearest_demand` (Long) or `nearest_supply` (Short); falls back to VWAP, then current_price.
Blocked when `chase_dist > IT_MAX_CHASE_ATR_MULT × atr` (default 1.5).

## Setup expiration (expires_at)
- LIQUIDITY_SWEEP_REVERSAL: 30 min
- BREAKOUT_RETEST: 45 min
- TREND_PULLBACK: 60 min

## _it_entry_veto_reasons reads from it_ctx, not DB
The daily cap check inside `_it_entry_veto_reasons()` reads:
- `it_ctx.get("daily_trade_count")` and `it_ctx.get("daily_trade_cap")`
These are populated by `compute_intraday_trend_context()` calling `_it_daily_trade_count()`.
**Why:** Test fixtures that inject cap state must use `ctx["daily_trade_count"] = N` + `ctx["daily_trade_cap"] = N` directly (not `ctx["daily_count"]`). Patching `_it_daily_trade_count` is irrelevant because veto_reasons never calls it.

## Test fixture pitfall: nearest_demand/chase
If `nearest_demand = price - 50` and `current_price = price`, `chase_dist = 50`.
With ATR=30, limit=45 → chase fires and blocks the test!
**Fix:** use `nearest_demand=None` (falls back to VWAP) to zero out chase_dist in success-path tests.

## full_analysis pre-compute
`_it_ctx` is computed BEFORE `build_strict_trade_plan` is called (when strict_label is actionable).
The IT veto block reuses `_it_ctx` from this pre-compute; it no longer initialises inside the veto block.
Shadow paths (lines ~48462, 48692) call `build_strict_trade_plan(mode=mode)` without `it_ctx` → return `IT_UNAVAILABLE` (correct: shadow just shows WAIT).

## Tests
49 tests in `artifacts/tradingview-webhook/test_intraday_engine.py`; all pass.
All 4 smokes (parity, scalp_golden, dual_sim, breakout_mode) pass post-implementation.
