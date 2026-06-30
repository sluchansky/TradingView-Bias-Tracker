---
name: Bot Training Mode suppresses live auto-trades
description: Second top cause (after un-armed) of "setups go READY but no trades fire" — training mode below Stage 4 suppresses every send.
---

# Bot Training Mode suppresses live auto-trades

When `TRAINING_MODE_ENABLED` is truthy, `_training_gate` is the single money-path
chokepoint inside `execute_trade_gateway`. Stages 1/2/3 (`training_stage()` reads
`bot_training_state.stage`, fail-closed to Stage 1) are **suggest-only**: they record
the would-be trade to the training ledger and return
`{"status":"manual_required", ...}` with NO top-level `reason`. The auto path then
logs `Auto-trade no-op for <inst> - gateway manual_required: None` and places NO
broker order. Only **Stage 4** falls through to the real live send.

**Why:** an intentional staged-autonomy safety gate (operator-built). It is ON in
production by env flag, independent of EXECUTION_MODE and of auto-trade arming — so
even a correctly armed instrument with a valid live provider takes zero trades while
below Stage 4.

**How to apply / diagnose "signal READY all night but no trades":** there are now
TWO top causes — check both.
1. Auto-trade not armed (resets OFF on every republish — see
   `auto-trade-arming-lifecycle.md`). `GET /auto-trade`.
2. Training mode suppressing: a `gateway manual_required: None` no-op in the logs +
   `TRAINING_MODE_ENABLED=1` (prod env) + `bot_training_state.stage < 4`.
   NOTE the log signature `manual_required: None` is SHARED by training-suppress AND
   `EXECUTION_MODE=manual_only`; distinguish by the absence of the
   `"Execution manual_only — plan returned"` log line and by the resolved mode
   (EXECUTION_MODE unset + TRADERSPOST_WEBHOOK_URL set ⇒ live `traderspost`, so
   manual_required there must be training-suppress).

**To actually trade live (money-path change — confirm with operator first):** either
advance `bot_training_state.stage` to 4, or remove/zero the `TRAINING_MODE_ENABLED`
prod env var (gate never called ⇒ legacy live path). Both start placing REAL orders.
