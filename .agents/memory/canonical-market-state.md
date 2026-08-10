---
name: Canonical Databento Market State Engine
description: Shadow VWAP/ATR/structure/sweep/CVD engine that computes Databento-derived state for comparison against legacy sources. Shadow-only, never promotes to live in Phase 1.
---

## Rule
All source selectors default to LEGACY. No component may reach LIVE_CANONICAL in this phase.
A missing env var CANNOT promote Databento to live.

**Why:** Safety contract from spec — production money-path must not change until validation data accumulates.

**How to apply:** When adding new computed values, add them to the snapshot as `promotion_status: SHADOW` only. Any promotion to VALIDATING requires explicit operator env var + agreement threshold check.

## Architecture
- `canonical_market_state.py` — standalone module (no app.py imports)
- Per-instrument `CanonicalMarketStateEngine` class — owns VWAP, ATR, structure, sweeps
- Boot via `cms.start(databento_bars_by_inst, cvd_by_ticker, rvol_by_ticker, vwap_by_ticker, get_db_fn=get_db_connection)` at app.py line ~80275
- Bar-close callback: `cms.on_bar_close(inst, close_price)` reads full bar from `DATABENTO_BARS_BY_INST[inst][-1]`
- Flask route: `GET /canonical-market-state?instrument=MNQ` (all instruments without param)
- Comparison table: `market_state_source_comparisons` (created at boot via `ensure_comparison_table`)
- Dashboard panel: `CanonicalStatePanel` in Analysis tab — fetches /api/canonical-market-state every 30s

## What is REUSED (not reimplemented)
- CVD: reads from `CVD_BY_TICKER[inst]` (DatabentoBrain already computed)
- RVOL: reads from `RVOL_BY_TICKER[inst]` (DatabentoBrain already computed)
- 15m/4H trend: consumed from `trend_alignment.MTF_STATE_BY_INST`
- FVG zones: consumed from `fvg_engine.FVG_ZONES_BY_INST`
- ORB: not reimplemented — read existing ORB state if needed

## What is NEW (computed from bars)
- Session VWAP: `pv_sum / v_sum` with reset at SESSION_RESET_UTC_HOUR=22 boundary
- ATR (Wilder's, period=14): from `DATABENTO_BARS_BY_INST` full bars
- Swing pivot detection: SWING_LOOKBACK=5 bars each side, WARMUP_BARS=25
- BOS / CHoCH: close breaks through confirmed swing high/low
- Liquidity sweeps: wick beyond swing level + close back inside

## Key constraints
- Bar-close callback signature is `(inst, close_price)` — NOT the full bar dict
  → must read full bar from `DATABENTO_BARS_BY_INST[inst][-1]`
- Structure health with no pivots detected = INSUFFICIENT_HISTORY (not DATA_UNAVAILABLE)
  — "no pivots yet" is always a timing issue, never a config problem
- Session VWAP reset: at UTC hour 22 (6 PM ET summer) — matches existing session logic
- DB accessor is `get_db_connection()` (not `get_db`)

## Tests
56 tests in `test_canonical_market_state.py`. Key test patterns:
- Determinism: `replay_bars(inst, bars)` API for clean replay without module state
- Feature flags: assert all source selectors == 'legacy' by default
- Thread safety: concurrent writer + reader threads
- LIVE_CANONICAL must never appear in any component's promotion_status

## Feature flags
```
CANONICAL_MARKET_STATE_ENABLED=1   (default 1)
CANONICAL_MARKET_STATE_SHADOW_ONLY=1 (default 1)
VWAP_SOURCE=legacy   # change to 'databento' when ready to promote
STRUCTURE_SOURCE=legacy
CVD_SOURCE=legacy
SWEEP_SOURCE=legacy
FVG_SOURCE=legacy
ZONE_SOURCE=legacy
```

## Next phase (not yet implemented)
- Source agreement threshold checks (e.g. VWAP within 1 tick for 50 bars → VALIDATING)
- Promotion path: SHADOW → VALIDATING → READY_FOR_PROMOTION
- UI: source comparison history viewer
- Apply comparison table schema to production (new table needs publish/re-deploy)
