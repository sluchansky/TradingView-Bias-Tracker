---
name: BOT TRAINING MODE (staged autonomy gate)
description: Flag-gated 4-stage controller + single fail-closed training gate that suppresses live sends until the bot's data proves out. Phase-1 semantics, persistence, and the lazy-probe gotcha.
---

# BOT TRAINING MODE

A flag-gated layer that trains/validates the live-money bot toward autonomy WITHOUT
changing strategy logic. Master flag `TRAINING_MODE_ENABLED` (env, default 0). When
OFF the gate is never reached → byte-identical (guarded by `training_gate_smoke` +
scalp/swing_flagoff/parity/learning_score goldens). Prod sets it `=1`; dev stays unset
so goldens/dev stay byte-identical.

## Gate placement & semantics (Phase 1)
- Single `_training_gate(intent, inst, source)` runs at the TOP of
  `execute_trade_gateway`, AFTER the prop-guard 409 / BEFORE the manual_only branch
  (i.e. before dedupe + `_send_broker_order`).
- Stages 1/2/3 → record `"suggested"` + return `_training_suppressed_result`
  (status `manual_required`, NEVER sends). `_maybe_auto_execute` treats only
  `sent`/`simulated` as fills, so `manual_required` is a clean no-op.
- Stage >= 4 → record `"auto_passthrough"` + return None → legacy live send path runs.
- DB/state unavailable while enabled → FAIL-CLOSED: suppress as Stage 1, write NO
  ledger row (so a DB outage can never both block AND silently look "recorded").

## Persistence (INSERT/SELECT only — app does NO DDL)
- `bot_training_state` (singleton row id=1: stage, desired_market, promoted_by/at, notes)
  + `bot_training_trades` ledger (ts, stage, session_key, market, setup, source,
  direction, entry/stop/target, planned_risk_dollars, status, *_hit_at, sim_outcome,
  real_order_id, broker_status, invalidation, grade_json).
- Dev tables created via the database tool; prod via Publish schema-diff. Prod state
  row is NOT seeded → Phase 1 deliberately relies on fail-closed → Stage 1.
- `_TRAINING_STATE_CACHE` has a ~5s TTL. **Promotion/demotion endpoints MUST
  invalidate / force-refresh it** or an emergency demote won't take effect for 5s.

## TRAINING_BOOT_STAGE env var (added to fix "stage resets after republish")
**Why it resets:** `TRAINING_MODE_ENABLED=1` is production-only, so the training panel
is hidden in dev. The user cannot set the stage from dev. After each republish they
would need to manually click "Go LIVE" in the prod dashboard — easy to miss.
The DB row was seeded at Stage 1 at build time and had never been updated.
**Fix:** `TRAINING_BOOT_STAGE=4` in the **production** env vars. On every boot, if
training is enabled, `_check_bot_training_db_ready()` UPSERTs the singleton row to
that stage. This survives every republish without requiring a manual dashboard click.
**How to apply:** set `TRAINING_BOOT_STAGE=<1-4>` in production only (never shared —
sharing would activate it in dev and break goldens since TRAINING_MODE_ENABLED must
stay prod-only). Current prod value: `TRAINING_BOOT_STAGE=4`.

## Lazy-probe gotcha (why `_ensure_bot_training_probe` exists)
**Why:** the boot readiness probe `_check_bot_training_db_ready()` is registered under
`if __name__ == "__main__"`. Prod launches Flask via a supervisor; if that ever imports
app as a module (WSGI) instead of running it as `__main__`, `BOT_TRAINING_DB_READY`
stays False → the gate still fails-closed/no-send but the ledger captures NOTHING,
silently defeating the whole point (collecting suggestions to prove the bot out).
**How to apply:** `_training_gate` calls `_ensure_bot_training_probe()` when not-ready —
a throttled (30s) lazy probe so a genuinely-missing table doesn't re-probe every webhook.
Only reachable when training is enabled, so flag-OFF stays byte-identical. NOTE: any ON
smoke that simulates "DB down" by setting `BOT_TRAINING_DB_READY=False` must also stub
`_ensure_bot_training_probe` to a no-op, else the real dev tables revive readiness.

## Max-loss policy
Keep the existing tighter $100/trade default; clamp the training cap to <= $200
(`TRAINING_MAX_RISK_DOLLARS`). Never raise the live default.

## Grading watcher (T003) — DISPLAY/ANALYTICS-ONLY
- `_watch_training_trades` resolves recorded suggestions into the ledger; it writes
  ONLY the grading columns of `bot_training_trades` (sim_outcome, *_hit_at, grade_json)
  and NEVER touches MANAGED_TRADES / strategy_trades / the money path. Own timer
  (`_training_grade_watch_loop`), self-gated on `training_mode_enabled()` + DB-ready +
  single-flight, so dev / flag-off never grade → byte-identical (the boot timer is only
  registered when the flag is on; goldens never run the boot block anyway).
- `_training_grade_call` reuses `_scalp_sim_outcome` STOP-FIRST (worst-case) and models
  the suggestion as a MARKET fill at `entry` (so entry_hit is always true; entry_hit_at
  = COALESCE(entry_hit_at, ts)). Past `TRAINING_GRADE_MAX_HOLD_HOURS` with no level hit →
  `expired` at the close, timing early(favorable)/late(unfavorable).
- **There is NO `r_multiple` column** — R + exit_price + timing + direction_correct live
  inside `grade_json` (jsonb via `psycopg2.extras.Json`); only the label goes in
  `sim_outcome`.
- The UPDATE's `WHERE sim_outcome IS NULL` IS the cross-instance claim (dev+prod share
  the DB) — first writer wins, racer no-ops. Same-bar guard skips a bar that opened
  at/before `ts` (no look-ahead self-fill), mirroring the scalp sim. FAIL-OPEN.

## Proof metrics + read endpoints (T004) — DISPLAY/READ-ONLY
- `GET /training/status` (controller state: enabled, stage + label/description,
  desired_market, db_ready, counts {total,graded,pending}) and `GET /training/metrics`
  (paper-graded performance) are owner-only and whitelisted in BOTH `flask-proxy.ts`
  AND mounted under `/api` (so the live URL is `/api/training/status`); they are NOT in
  dashboard-auth `OPEN_PATHS` (auth required). Raw `curl $REPLIT_DEV_DOMAIN/...` returns
  `000` — the preview proxy is mTLS, so verify endpoints via the Flask test client, not curl.
- Math lives in pure helpers so it is unit-testable without a DB: `_tr_grade` coerces
  `grade_json` whether it arrives as a dict OR a JSON string (DB vs. test); `_training_agg`
  computes win_rate over DECIDED (win+loss, excludes expired), but PF / expectancy /
  maxDD / avg-win / avg-loss / direction_accuracy over ALL numeric R (expired included).
  PF of a loss-only group is `0.0`; PF is `None` ONLY when there is zero R data; a
  no-loss group caps PF at `99.0`.
- `_training_compute_metrics` runs ONE SELECT of graded rows ordered by
  `COALESCE(entry_hit_at, ts)`, then buckets overall + per_stage + per_market, and ranks
  best/worst setup + best market + best ET-hour window. Ranking groups must clear
  `TRAINING_MIN_GROUP_N` (default 3) or they are excluded (so one lucky trade can't be
  "best"); a too-small group → that ranking key is `None`.
- Promotion thresholds are env-tunable via `_training_promotion_thresholds()`
  (`TRAINING_PROMOTE_MIN_TRADES`=20 / `_MIN_WIN_RATE`=50 / `_MIN_PF`=1.3 / `_MAX_DD_R`=6);
  eligibility returns 4 labelled pass/fail checks + an `eligible` bool. T004 only
  COMPUTES eligibility (display); actual promotion is T006.
- Dashboard `#mod-training` panel is hidden (`display:none`) until `/training/status`
  reports `enabled` — populated by `loadTraining()` (registered in the 3s poll + one
  immediate call), render is `textContent`/`_anFill` only (no innerHTML). Verified by
  `check_training_metrics.sh` (training_metrics_smoke.py 40 checks INCL a money-path
  tripwire that fails if either endpoint calls `_send_broker_order`, + a node --check of
  the SERVED dashboard `<script>`).
