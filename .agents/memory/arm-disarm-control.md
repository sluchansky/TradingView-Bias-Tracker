---
name: Execution Arm / Disarm Control
description: In-memory arm-state controller for live auto-execution. Safety layer requiring both EXECUTION_MODE=traderspost AND an active arm session for live orders.
---

# Execution Arm / Disarm Control

## Rule
Live auto-trades require BOTH `EXECUTION_MODE=traderspost` AND `_ARM_STATE["armed"] == True`. Always starts DISARMED. Never persisted across restarts.

**Why:** Belt-and-suspenders safety — execution mode alone is insufficient. Operator must explicitly arm each session.

## How to Apply
- 5-state model: `disabled`, `paper`, `live_available_disarmed`, `live_armed`, `safety_locked`
- `safety_locked` overrides all other states (checked first in `_effective_execution_state()`)
- Both pre-transmission gates gated on `execution_is_live(mode)` → paper mode byte-identical

## Key Locations
- Arm state block: `_ARM_STATE` dict, `_ARM_STATE_LOCK`, `_ARM_AUDIT_LOG`, defined after line 9540
- Pre-transmission gate 1: `_maybe_auto_execute()` after Databento health gate
- Pre-transmission gate 2: `_execute_trade_gateway_inner()` before `_send_broker_order()`
- Flask routes: `/execution/state|arm|disarm|kill-switch|reset-safety-lock|audit-log`
  - All decorated with `@_arm_owner_required` (Flask-level defense-in-depth auth check)
  - Express whitelist: all `/execution/*` routes in `BOT1_ROUTES` in flask-proxy.ts
- React panel: `ArmControlPanel` component in MainBrain.tsx, route `/main-brain/execution`
  - Polls `/api/execution/state` every 30s; backend sole source of truth
  - Nav item `{ id: 'execution', label: 'Execution', icon: '⊙' }` in navItems.ts
- DB audit: `execution_arm_audit` table (PostgreSQL); critical events persisted via `_persist_arm_audit_critical()`
  - `EXECUTION_ARM_AUDIT_DB_READY` flag; probe via `_probe_execution_arm_audit_table()` at boot
  - Uses `get_db_connection()` (NOT `db()`) — same as other probe functions
  - Called in boot sequence after `_check_dq_db_ready()`

## Flask Auth Decorator (`_arm_owner_required`)
- Checks `DASHBOARD_PASSWORD` env var + Basic-auth header
- **ONLY activates for non-localhost remote_addr** — Express always proxies from 127.0.0.1 (no check); test client also uses 127.0.0.1 (no check). Only fires if Flask somehow receives a request directly from a non-local IP (misconfiguration scenario).
- If DASHBOARD_PASSWORD not set → always passes (development fallback).

## Critical Constraints
- `_check_arm_for_transmission()` checks raw `_ARM_STATE` dict directly (NOT via `_effective_execution_state()`). Testable without patching `resolve_execution_mode`.
- `_disarm()` NEVER calls close/cancel functions. Existing protective stops survive.
- `/execution/kill-switch` ≠ position close. Separate "EMERGENCY CLOSE" not implemented — state clearly in UI.
- Session trade count via `_arm_increment_trades_used()` after confirmed send only.
- Contract limits: session can only TIGHTEN, never loosen.
- Armed state intentionally NOT restored on boot; audit events ARE (via DB).

## Arm confirmation phrase
`"ARM LIVE AUTO TRADING"` (exact, case-sensitive, POST body `confirm_phrase`)

## Auto-disarm triggers (watcher thread, every 30s)
- (A) Arm session expired
- (B) Databento disconnected
- (C) Daily loss limit breached
- (D) Session loss limit exceeded
- (E) LRE error count ≥ 10

## Tests
- `test_auto_trading_arm_control.py` — 74 tests, all pass
- `test_arm_inflight_race.py` — 25 tests (5 race scenarios × ~5 assertions each), all pass
  - Race 1: Disarm between checks blocks final gate
  - Race 2: Session expiry between checks blocks final gate
  - Race 3: Kill switch between checks blocks final gate
  - Race 4: Session replacement (old session ID mismatch) blocks new session
  - Race 5: Limit change (tighter contracts) blocks over-limit candidate
- `test_auto_trading_high_findings_remediation.py` — 77 tests, all pass
  - Fix: `_make_analysis()` needs `"edge_score": 90` or Asia-session Long gate blocks at 0 edge during overnight hours
- Combined: 273/273 pass

## DB Audit Schema
```sql
CREATE TABLE execution_arm_audit (
  id SERIAL PRIMARY KEY,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  action TEXT NOT NULL,           -- arm/disarm/safety_lock/reset_safety_lock/candidate_authorized/candidate_blocked_final
  reason TEXT,
  arm_session_id TEXT,
  by_actor TEXT,
  effective_state TEXT,
  extra JSONB
);
CREATE INDEX idx_arm_audit_recorded_at ON execution_arm_audit(recorded_at DESC);
```
