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
