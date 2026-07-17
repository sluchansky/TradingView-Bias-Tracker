---
name: Trade Failure Analyzer
description: TFA records every final-verdict READY decision → trigger → outcome → failure mode; 25-trade Discord summary; display-only.
---

## Rule
TFA is DISPLAY-ONLY, FAIL-OPEN, gated on `TFA_DB_READY` (False until boot probe succeeds → goldens byte-identical when DB absent).

**Why:** Recording every READY decision + outcome + failure mode reveals systematic weaknesses (WRONG_BIAS, POOR_LOCATION, VOLATILITY_MISMATCH, NOT_TRIGGERED, etc.) over a sample of trades.

## Key globals (line ~133)
- `TFA_DB_READY` — probe result; gates all TFA code
- `PENDING_TFA_BY_INST` — inst → ready_id linking READY → trigger (overwritten on each new READY)
- `LAST_TFA_BY_INST` — inst → {ready_id, direction, ts} for 5-min dedup
- `LIVE_TFA_BY_INST` — inst → ready_id of most-recently-triggered trade (popped at close)

## Function block (line ~27292)
`_check_tfa_db_ready`, `_make_tfa_ready_id`, `_record_ready_decision`, `_mark_tfa_triggered`, `_classify_failure_mode_tfa`, `_complete_tfa_record`, `_expire_stale_tfa_records`, `_generate_failure_summary`.

## Integration hooks
1. `full_analysis()` single return — record READY after ALL vetoes (uses local variables: `verdict`, `instrument_of(active_ticker)`, `strict_direction`, `edge_score`, `scalp_quality_block`, `bias`, `confluences`, `volatility`, `current_price`, `nearest_demand/supply`)
2. `_maybe_auto_execute()` — mark triggered from auto (source=source)
3. `/traderspost` route — mark triggered from manual ENTER (source="manual")
4. `_close_managed_trade()` — complete record via `LIVE_TFA_BY_INST.pop(inst)`; passes `mt.get("learning_ctx")` for bias/vol_regime/eq_score
5. `_alert_history_snapshot_loop()` — `_expire_stale_tfa_records()` every 30 iterations (30 min)
6. Boot sequence — `_check_tfa_db_ready()` before `_check_market_state_cache_db_ready()`

## DB table
`trade_failure_analysis` — INSERT/SELECT/UPDATE only (no DDL in app.py).
Created via database tool (dev) + Publish schema-diff (prod).
3 indexes: (instrument, ready_at), failure_mode, completed_at WHERE NOT NULL.

## Failure modes
Reuses `_derive_trade_label` categories (WIN, LATE_ENTRY, EARLY_ENTRY, STOPPED_BEFORE_MOVE, STOP_TOO_TIGHT, BAD_SETUP, BAD_SESSION, NO_FOLLOW_THROUGH, TP1_THEN_BE, BREAKEVEN, LOSS, UNCATEGORIZED) plus:
- `WRONG_BIAS` — direction opposite to bias at entry
- `POOR_LOCATION` — entry_quality < 60
- `VOLATILITY_MISMATCH` — extreme vol regime
- `NOT_TRIGGERED` — READY expired after 2h with no execution

## 25-trade summary
`_generate_failure_summary()` called via `_enqueue_slow` after every 25 completions.
Posts to `DISCORD_WEBHOOK_URL` gated on `DISCORD_LIVE_ENABLED`.

## Route
`/failure-analysis GET` — owner-only, added to Express proxy whitelist in `artifacts/api-server/src/routes/flask-proxy.ts`. Returns `{status, records, summary, totals{completed, wins, win_rate_pct}}`. Flask runs on PORT env (default 8000).

## Schema gap found during validation
The initial CREATE TABLE omitted the `outcome VARCHAR` column. Fixed via `ALTER TABLE trade_failure_analysis ADD COLUMN IF NOT EXISTS outcome VARCHAR`. Must be applied to production via Publish schema-diff before the first production deploy. Without it every `_complete_tfa_record()` call fails silently (fail-open — record stays incomplete forever).

## How to apply
- Any new READY trigger path (gateway variant, new instrument) must call `_mark_tfa_triggered(inst, source, entry_price)` after a `sent`/`simulated` status.
- Any new close path (ACTIVE_TRADE check_trade_events) should pop `LIVE_TFA_BY_INST.pop(inst)` and call `_complete_tfa_record`.
- A new failure mode = add a priority branch in `_classify_failure_mode_tfa` ABOVE the `base_label` fall-through.
