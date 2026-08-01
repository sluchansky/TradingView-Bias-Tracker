---
name: Execution Arm / Disarm Control
description: In-memory arm-state controller for live auto-execution. Safety layer requiring both EXECUTION_MODE=traderspost AND an active arm session for live orders.
---

# Execution Arm / Disarm Control

## Rule
Live auto-trades require BOTH `EXECUTION_MODE=traderspost` AND `_ARM_STATE["armed"] == True`. Always starts DISARMED. Never persisted.

**Why:** Belt-and-suspenders safety — execution mode alone is insufficient. Operator must explicitly arm each session.

## How to Apply
- 5-state model: `disabled`, `paper`, `live_available_disarmed`, `live_armed`, `safety_locked`
- `safety_locked` overrides all other states (checked first in `_effective_execution_state()`)
- Both pre-transmission gates gated on `execution_is_live(mode)` → paper mode byte-identical

## Key Locations
- Arm state block: after `_record_exec_attempt()` in app.py
- Pre-transmission gate 1: `_maybe_auto_execute()` after Databento health gate
- Pre-transmission gate 2: `_execute_trade_gateway_inner()` before `_send_broker_order()`
- Flask routes: `/execution/state|arm|disarm|kill-switch|reset-safety-lock|audit-log`
- Express whitelist: all `/execution/*` routes added to `BOT1_ROUTES` in flask-proxy.ts
- Dashboard panel: `div#mod-armctl` (data-cat="advanced"), polled every 30s

## Critical Constraints
- `_check_arm_for_transmission()` checks raw `_ARM_STATE` dict directly (NOT via `_effective_execution_state()`). This is intentional — callers are already gated on `execution_is_live(mode)`, and the direct check makes the function testable without patching `resolve_execution_mode`.
- `_disarm()` NEVER calls close/cancel functions. Existing protective stops survive.
- `/execution/kill-switch` ≠ position close. Separate "EMERGENCY CLOSE" needed.
- Session trade count in `_ARM_STATE["trades_used"]` — increment via `_arm_increment_trades_used()` after confirmed send.
- Contract limits are clamped to `max_contracts(inst)` — session can only TIGHTEN, never loosen.

## Arm confirmation phrase
`"ARM LIVE AUTO TRADING"` (exact, case-sensitive, POST body `confirm_phrase`)

## Auto-disarm triggers (watcher thread, every 30s)
- (A) Arm session expired
- (B) Databento disconnected
- (C) Daily loss limit breached
- (D) Session loss limit exceeded
- (E) LRE error count ≥ 10

## Tests
`test_auto_trading_arm_control.py` — 74 tests, all pass
