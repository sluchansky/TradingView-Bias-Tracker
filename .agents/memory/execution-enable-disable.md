---
name: Execution Enable/Disable control
description: The execution_enabled software switch and its state machine, gate integration, routes, and test pattern.
---

## The rule

`execution_enabled` (user toggle) and `armed` (session gate) are TWO independently stored booleans.
**Automated transmission requires BOTH: execution_enabled==True AND armed==True.**

## State machine

```
DISABLED (execution_enabled=False) → [POST /execution/enable] → ENABLED/DISARMED
ENABLED/DISARMED → [POST /execution/arm] → ENABLED/ARMED
ENABLED/ARMED → [POST /execution/disarm] → ENABLED/DISARMED
ANY enabled state → [POST /execution/disable] → DISABLED (also disarms)
```

## Backend: _ARM_STATE keys added

- `_ARM_STATE["execution_enabled"]` — bool, default False.
- `RC_EXECUTION_DISABLED` — new reason code returned by `_check_arm_for_transmission` when disabled.
- Check 0 in `_check_arm_for_transmission` checks `execution_enabled` BEFORE check 1 (safety_locked) and check 2 (armed).

## Routes

- `POST /execution/enable` — body: `{"confirm_phrase": "ENABLE AUTO TRADING"}` → 200 or 400
- `POST /execution/disable` — body: `{"reason": "operator_manual"}` → 200, also disarms
- Both in `_CRITICAL_ARM_ACTIONS` (persist to DB).
- Both require auth (NOT in OPEN_PATHS, added to BOT1_ROUTES in flask-proxy.ts).

## Persistence

- `_restore_execution_enabled_from_db()` called at boot after `_probe_execution_arm_audit_table()`.
- Reads last `action IN ('enable', 'disable')` from `execution_arm_audit` table.
- Column is `recorded_at` NOT `created_at` (confirmed from schema).
- `armed` always resets to False on restart (no restore for arm session).

## /execution/state response

Now includes: `execution_enabled: bool`, `active_trade_count: int`.

## Test helpers that arm the state (execution_enabled=True required)

Both `_arm_session()` helpers in test files must include `"execution_enabled": True` when setting `armed=True` directly in `_ARM_STATE`. Without it, `_check_arm_for_transmission` returns `RC_EXECUTION_DISABLED`.

Files updated: `test_auto_trading_arm_control.py`, `test_arm_inflight_race.py`.

## Disarmed gate test

`test_no_live_order_when_disarmed_even_with_traderspost_mode` now sets `execution_enabled=True` before calling the gate so the check reaches the disarmed (check 2) path specifically.

## New test file

`test_execution_enable_disable.py` — 40 tests in isolation. Do NOT set `FLASK.config["TESTING"] = True` at module level (breaks full-suite collection when other test files have already imported app.py).

## /execution/arm change

ARM returns 409 RC_EXECUTION_DISABLED if `execution_enabled=False` (checked before `_arm_preflight_check`).

**Why:** Security-first ordering. The preflight check (mode, contracts, etc.) shouldn't run until the basic software switch is on.

**How to apply:** Any test that calls the `/execution/arm` route must call `/execution/enable` first (or mock `_ARM_STATE["execution_enabled"] = True`). Rate limiter (`_ARM_RATE_LIMIT`) must be cleared in setUp when tests call /execution/arm in rapid succession.

## Explicit disabled deployment pin

When `EXECUTION_MODE=disabled` is explicitly configured for a deployment, it is authoritative over both the persisted arm-audit enable state and any persisted runtime execution-mode override. Boot must leave the software switch off, clear the in-memory override, and reject an attempted live runtime-mode change without saving it.

**Why:** Market-state restore runs after arm-audit restore. Without this priority rule, an older `traderspost` or `pickmytrade` override can reappear late in boot and silently re-open a live-capable route during a safety republish.

**How to apply:** Treat a disabled deployment as a durable operational boundary, not a UI state. Verify cold boot logs show both restore suppressions and verify `/execution/state` reports `effective_mode=disabled`, `execution_enabled=false`, `armed=false`, and no runtime mode override.
