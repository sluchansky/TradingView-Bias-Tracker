---
name: INTRADAY_TREND Phase 2 gap closure
description: All 14 functional gaps from the Phase 2 audit, what was built, what tests cover it, and what prod still needs.
---

## What was built (shadow/display-only; SWING byte-identical)

### DB migration (dev only; needs Publish for prod)
- `ghost_observations` gained 7 nullable IT columns:
  `it_touched_1r`, `it_touched_2r`, `it_touched_3r`,
  `it_max_favorable_pts`, `it_max_adverse_pts`,
  `it_over_100pts`, `it_mgmt_premature_exit`

### New helpers (in app.py)
| Helper | Purpose |
|---|---|
| `_it_compute_session_levels(instrument, et_now)` | Asia/London/overnight/OR/NY + 3-bar pivot swings from live bars; reads `databento_brain.DATABENTO_BARS_BY_INST` via local import |
| `_it_structural_stop(family, confs, dir, price, levels, atr)` | Per-family structural stop with 0.15×ATR buffer (min 2 pts); FAIL-OPEN → None |
| `_it_confirmation_complete(family, confs, dir, score)` | 3-step checklist per family (LSR/BREAKOUT_RETEST/TREND_PULLBACK); None family → (False,[],[…]) |
| `_it_risk_sizing(stop_pts, instrument)` | floor(MAX_RISK_DOLLARS / (stop × pv)), capped 1–4; zero/neg stop → (1, None) |
| `_it_daily_trade_count(instrument)` | Queries ghost_observations for today's IT entries; count=-1 on error (fail-open) |
| `compute_it_trade_management(active, price, it_ctx, et_now)` | Advisory management engine; FORCE_FLAT at 15:55 ET, PARTIAL_1R5 at 2c, PARTIAL_2R at 3c, CLOSE_STRUCTURE on strong inversion below 0.5R |
| `_it_force_close_watchdog()` | Called by heartbeat; noop before 15:55 ET, closes open IT ghost_obs after and writes extended fields |
| `_it_ghost_write_extended_fields(conn, obs_id, …)` | Computes + writes 7 IT ghost columns; premature_exit = mfe_r≥1.5 AND gross_r < 0.5×mfe_r |

### Modified functions
- `compute_intraday_trend_context()` — 18 new Phase 2 ctx keys; DAILY_CAP_REACHED / AWAITING_CONFIRMATION / CONFIRMED_SETUP status states
- `_it_entry_veto_reasons()` — 5 gates total (was 3); gate 4 = confirmation incomplete, gate 5 = daily cap (fail-open when count==-1)
- `_it_diag_block()` — passes all 18 Phase 2 keys through
- `_run_heartbeat_evaluations()` — calls watchdog when `TRADING_MODE == "INTRADAY_TREND"`
- Ghost watcher close path — calls extended-fields writer when `row.trading_mode == "INTRADAY_TREND"`
- Dashboard `swd-it-content` block — Phase 2 grid row + conf steps + mgmt advisory panel

## Tests
`artifacts/tradingview-webhook/tests/test_intraday_trend_phase2.py` — 105 tests, all pass.

Covers: confirmation (15 tests), structural stop (10), risk sizing (7), daily count (6),
trade management (20), force-close watchdog (4), ghost extended fields (10), ctx Phase 2 schema (8),
veto gates 4&5 (13), session levels (8), parity (4).

## Mocking notes
- `_it_compute_session_levels` does `from databento_brain import DATABENTO_BARS_BY_INST` inside the function.
  Tests must mock with `patch.dict(sys.modules, {"databento_brain": MagicMock(DATABENTO_BARS_BY_INST=…)})`.
  `patch("app.DATABENTO_BARS_BY_INST", …)` does NOT work.
- Status chain: `BLOCKED_MID_RANGE` (location gate) fires BEFORE `AWAITING_CONFIRMATION` / `CONFIRMED_SETUP`.
  Tests that check confirmation state should assert on `confirmation_complete` flag, not status string.

## Native journal mode tagging
Already works: native journal INSERT reads `snapshot.get("mode")` (line ~37439);
snapshots are built with `"mode": TRADING_MODE` — no gap.

## What still needs prod
- Re-Publish the app so the 7 new ghost_observations columns are applied to the production DB.
- Until then, the extended IT fields silently no-op in prod (INSERT column list mismatch → error swallowed).

**Why:** All gaps were shadow/display only so no live behavior changes until a Publish is done.
