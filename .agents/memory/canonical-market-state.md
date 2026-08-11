---
name: Canonical Databento Market State Engine
description: Shadow VWAP/ATR/structure/CVD engine. Key constraints, DST fix, provenance audit, ORB wiring.
---

## Safety
- All source selectors default to LEGACY — missing env var cannot promote Databento to live.
- `CANONICAL_MARKET_STATE_SHADOW_ONLY=1` default; `LIVE_CANONICAL` never reachable this phase.

## Bar-close callback
- Signature: `on_bar_close(inst, close_price)` — reads full bar from `DATABENTO_BARS_BY_INST[inst][-1]`
- DB accessor: `get_db_connection()` (not `get_db`)

## VWAP session reset — DST FIX (important)
- Uses `zoneinfo.ZoneInfo("America/New_York")` for 18:00 ET boundary.
- EDT (summer, UTC-4): reset = 22:00 UTC. EST (winter, UTC-5): reset = 23:00 UTC.
- Old code used fixed `SESSION_RESET_UTC_HOUR=22` — was one hour early in winter.
- `_session_start()` is the single computation point; tests cover both EDT and EST.
- Session tests must use concrete calendar dates (June = EDT, January = EST) not abstract timestamps.

## `start()` signature
```python
cms.start(
    databento_bars_by_inst=_DB_BARS,
    cvd_by_ticker=CVD_BY_TICKER,
    rvol_by_ticker=RVOL_BY_TICKER,
    vwap_by_ticker=AUTO_PRICE_BY_TICKER,
    intraday_by_ticker=INTRADAY_BY_TICKER,   # ORB state — TradingView-sourced
    get_db_fn=get_db_connection,
)
```

## Provenance audit — TRUE sources
| Component | True source | Notes |
|-----------|-------------|-------|
| CVD | `databento_primary` | DatabentoBrain 1m bar accumulator (primary); TV alerts may inject |
| RVOL | `databento_primary` | DatabentoBrain 1m bar computation; TV alerts may inject |
| 15m/4H trend | `databento` | trend_alignment.ingest_1m_bar() fed by DATABENTO_BARS_BY_INST |
| FVG zones | `databento` | fvg_engine.process_bar_close() fed by DATABENTO_BARS_BY_INST |
| Zone state | `tradingview` | TradingView ALERT_HISTORY → get_price_context(); NOT promotable to Databento |
| ORB state | `tradingview` | INTRADAY_BY_TICKER from TV webhook price path; NOT promotable |

Zone state and FVG zones are SEPARATE things — do not conflate.

## ORB exposure
- Reads `_INTRADAY_BY_TICKER` (injected at boot) for `or_high/or_low/or_complete`.
- Reads `_BREAKOUT_OR_BY_TICKER` for 09:30 ET ORB (not yet injected — optional).
- Both carry `promotion_status = UNAVAILABLE_FOR_DATABENTO_PROMOTION`.
- If not injected: returns `status = UNAVAILABLE` + reason.

## VWAP comparison block fields
All required fields in `vwap_comparison`:
`legacy_vwap, legacy_source, legacy_freshness, databento_vwap, databento_source,
databento_freshness, absolute_difference, tick_difference, tick_size,
agreement_status, session_start, sample_volume,
sample_count, consecutive_acceptable, avg_tick_diff, max_tick_diff, pct_within_tolerance`

Agreement status enum: `MATCH (≤2 tk) | SMALL_DIFF (≤10 tk) | LARGE_DIFF (>10 tk) | WAITING | STALE | UNAVAILABLE`

Tick sizes: MGC=0.10, MNQ=0.25, MES=0.25, MYM=1.00

## Comparison stats
`_VwapStats` per instrument, reset on restart. `get_vwap_comparison_stats(inst?)` public API.
Tolerance threshold: 5 ticks for `pct_within_tolerance`.

## Structure health
- No pivots = `INSUFFICIENT_HISTORY` (not `DATA_UNAVAILABLE`).

## Test count
- 84 tests total (56 original + 28 new: 8 DST + 9 provenance + 11 comparison metrics).

## UI — 4-instrument comparison panel
- `CanonicalStatePanel` in MainBrain.tsx fetches `/api/canonical-market-state` (no `?instrument=`).
- Shows all 4 instruments side-by-side with VWAP comparison + rolling metrics sub-table.
- Agreement color: MATCH=green, SMALL_DIFF=yellow, LARGE_DIFF=red, STALE=orange, WAITING=gray.
