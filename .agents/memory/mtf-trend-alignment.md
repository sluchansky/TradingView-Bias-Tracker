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
- Trend: EMA(8) vs EMA(21) on **closed** bars; 0.03% neutral band.
- Staleness: > 30 min for 15M → STALE; > 8h for 4H → STALE.

## Key public API
- `ingest_1m_bar(instrument, bar)` — fail-open; accumulates into 15M/4H buckets; closes bucket on next bar.
- `seed_from_1m_bars(instrument, bars_1m)` — bulk-seed from historical 1m bars at boot.
- `get_alignment(t4h, t15m)` → ALIGNED_LONG / ALIGNED_SHORT / CONFLICTING / MIXED / STALE / UNAVAILABLE.
- `get_mtf_state(instrument)` — full state dict for API; fail-open.
- `get_snapshot_for_signal(instrument)` → `{four_h_trend_at_signal, fifteen_m_trend_at_signal, trend_alignment_at_signal}` — frozen at ghost signal time.

## Integration points in app.py
1. `_mtf_bar_close(instrument, bar)` — registered as bar-close callback via `register_bar_close_callback`.
2. Boot seeds from `DATABENTO_BARS_BY_INST` after registration.
3. `_get_mtf_snapshot_at_signal(inst)` — called at top of `_ghost_observe_setup`; result (`_mtf_snap`) passed to both ghost_obs INSERT and `_el_create_entry`.
4. Ghost `ghost_observations` INSERT: 3 new TEXT columns (`four_h_trend_at_signal`, `fifteen_m_trend_at_signal`, `trend_alignment_at_signal`) + 3 params.
5. Edge Ledger `edge_ledger` INSERT: same 3 columns + params via `mtf_snap` kwarg.
6. Flask route: `GET /market/trend-alignment?instrument=MNQ` — in proxy whitelist (`BOT1_ROUTES`).
7. Dashboard: `#mb-mtf-trend` card in Brain tab; `mtfLoad()`/`mtfRender()` JS; 30s poll IIFE.

## DB migration
`db_mtf_schema_patch.sql` adds the 6 columns (3 per table) with `ADD COLUMN IF NOT EXISTS TEXT`. Applied to dev DB. Needs re-publish for production.

## Why
**Why:** Stale test fix — seeding 1440 bars starting 24h ago covers the last 24h; last bar ends at ~now → not stale. Tests must start bars at 48h ago (or earlier) so the 24h run ends 24h in the past, exceeding both STALE thresholds.

## Tests
48 tests in `tests/test_phase8b1_mtf_alignment.py` + 4 golden subtests all pass.

## Key invariant
- CLOSED bars only: partial (forming) bucket is never promoted to trend calculation.
- Fail-open everywhere: no exception in MTF code ever blocks or errors the ghost/EL pipeline.
- SCALP golden byte-identical: trend context is additive only.
