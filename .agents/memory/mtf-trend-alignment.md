---
name: MTF Trend Alignment (Phase 8B.1)
description: Multi-timeframe 4H/15M trend display layer sourced from Databento 1m bars. DISPLAY-ONLY, fail-open.
---

# MTF Trend Alignment — Phase 8B.1

## What it does
Computes whether MNQ (and other instruments) are trending BULLISH / BEARISH / NEUTRAL on the **4-hour** and **15-minute** timeframes, and whether those two frames are ALIGNED or CONFLICTING. Purely informational — no gate, no scoring, no execution.

## Architecture
- **`trend_alignment.py`** — pure module (no app.py imports). Maintains `MTF_STATE_BY_INST` dict per instrument.
- Source: Databento 1m bars only (no Yahoo, no TradingView).
- Trend: EMA(8) vs EMA(21) on **closed** bars; 0.03% neutral band. **EMA(21) requires ≥21 closed bars** at each timeframe — plan around this threshold.
- Staleness: > 30 min for 15M → STALE; > 8h for 4H → STALE internally.
  Operator-facing stale data must be non-directional and unavailable, with
  clear age and source context.

## Key public API
- `ingest_1m_bar(instrument, bar)` — fail-open; accumulates into 15M/4H buckets; closes bucket on next bar.
- `seed_from_1m_bars(instrument, bars_1m)` — bulk-seed from historical 1m bars at boot.
- `get_alignment(t4h, t15m)` → ALIGNED_LONG / ALIGNED_SHORT / CONFLICTING / MIXED / STALE / UNAVAILABLE.
- `get_mtf_state(instrument)` — full state dict for API; fail-open.
- `get_snapshot_for_signal(instrument)` → `{four_h_trend_at_signal, fifteen_m_trend_at_signal, trend_alignment_at_signal}` — frozen at ghost signal time.

## Integration points in app.py
1. `_mtf_bar_close(instrument, _close_price)` — registered as bar-close callback. Reads full bar from `DATABENTO_BARS_BY_INST[inst][-1]` (not the float close price passed by the callback).
2. `_seed_mtf_from_historical()` — daemon thread at boot; fetches 80h Databento historical OHLCV; fires 10s after callback registration.
3. `_get_mtf_snapshot_at_signal(inst)` — called at top of `_ghost_observe_setup`; result passed to ghost_obs and edge_ledger INSERTs.
4. Ghost `ghost_observations` INSERT: 3 TEXT columns (`four_h_trend_at_signal`, `fifteen_m_trend_at_signal`, `trend_alignment_at_signal`).
5. Edge Ledger `edge_ledger` INSERT: same 3 columns via `mtf_snap` kwarg.
6. Flask route: `GET /market/trend-alignment?instrument=MNQ` — in proxy whitelist (`BOT1_ROUTES`).
7. Frontend: `MTFTrendPanel` React component in `MainBrain.tsx`; polls `/api/market/trend-alignment?instrument={ticker}` every 30s.

## DB migration
`db_mtf_schema_patch.sql` adds the 6 columns (3 per table) with `ADD COLUMN IF NOT EXISTS TEXT`. Applied to dev DB. Needs re-publish for production.

## Critical bug: callback passes close price, not bar dict
`DatabentoBrain._on_bar_close` calls `_cb(inst, bars[-1]["close"])` — second argument is a **float**, NOT the full bar dict. `_mtf_bar_close` reads the full bar from `DATABENTO_BARS_BY_INST[inst][-1]` instead. Any future bar-close callback must account for this.

## Historical seed: three bugs found & fixed
1. **`end` too recent**: Databento historical API lags ~5 min. Setting `end=now` → `422 data_end_after_available_end`. Fix: `end = now - timedelta(minutes=10)`.
2. **`ts_event` is DataFrame index, not column**: `store.to_df()` puts the timestamp as the pandas index. `row.get("ts_event", 0)` always returns 0, so all bars fail `bar_ts <= 0` guard. Fix: use `for ts_idx, row in df.iterrows()` then `ts_idx.timestamp()`.
3. **Window too narrow for EMA(21)**: 52h window over a weekend gap only yields ~180 bars (~12 closed 15M). EMA(21) needs ≥21 closed 15M bars = 315 trading minutes. Fix: extend to **80h** which captures the full Friday Globex session (~1380 bars → ~92 closed 15M bars).

## Prices from to_df() are already floats
`ohlcv-1m` records via `store.to_df()` return prices as floats (e.g. `29800.5`), NOT fixed-point int64. The fixed-point divide-by-1e9 only applies when iterating raw records directly.

## Weekend behavior (expected)
On Sunday restarts, historical 15M/4H bars can be stale while a new live stream
is reconnecting. This is correct and expected — frontend renders the trend as
`UNAVAILABLE` with a stale badge and exact age, not an old directional label.
4H resumes once a current 4H bar closes.

## Tests
48 tests in `tests/test_phase8b1_mtf_alignment.py` + 4 golden subtests all pass.

## Key invariants
- CLOSED bars only: partial (forming) bucket is never promoted to trend calculation.
- Fail-open everywhere: no exception ever blocks the ghost/EL pipeline.
- Public stale-trend safety: stale or failed shadow trend data must never
  display as directional guidance; show an unavailable state with freshness,
  age, source, and a safe error status instead.
- SCALP golden byte-identical: trend context is additive only.
