# AUTO-TRADING ARM / DISARM CONTROL — Implementation Report
## Spec §15 Final Report

**Implementation date:** 2026-08-01  
**Branch:** `polish-v1`  
**Commit:** `3408255`

---

## 1. Summary

The Execution Arm/Disarm Control System has been fully implemented as a mandatory safety layer on top of the existing execution infrastructure. Live auto-trades now require **both** `EXECUTION_MODE=traderspost` AND an active, unexpired operator arm session. The system always starts **DISARMED** on every restart, deploy, crash, or configuration reload.

---

## 2. Effective Execution States (Spec §2)

| State | Condition |
|---|---|
| `disabled` | EXECUTION_MODE=disabled or unset |
| `paper` | EXECUTION_MODE=paper or manual_only |
| `live_available_disarmed` | traderspost/pickmytrade, not armed |
| `live_armed` | traderspost/pickmytrade, armed, unexpired |
| `safety_locked` | Kill switch engaged (overrides all other states) |

---

## 3. Components Delivered

### 3.1 Backend Arm-State Controller (app.py)

**Data structures:**
- `_ARM_STATE` — 20-field in-memory state dict (never persisted)
- `_ARM_STATE_LOCK` — `threading.RLock()` for all mutations
- `_ARM_AUDIT_LOG` — `deque(maxlen=500)` of structured change records
- `_ARM_RATE_LIMIT` — dict keyed by IP, pruned per request (max 5/5min)

**State management functions:**
- `_effective_execution_state()` — 5-state machine; safety_locked checked first
- `_disarm(reason, by)` — thread-safe, always succeeds, records audit
- `_safety_lock(reason, by)` — thread-safe, disarms + locks, logs CRITICAL
- `_reset_safety_lock(by)` — validates no unknown protective-order state first
- `_record_arm_audit(action, prev, new, **kwargs)` — fail-open, redacts secrets
- `_arm_preflight_check(data)` — 8-point check (mode, lock, URL, Databento, daily loss, active positions, instruments, contract limits)
- `_check_arm_for_transmission(inst, contracts, strategy, session_id, direction)` — 9-point fail-closed gate; checks raw arm state (not mode-dependent)
- `_arm_increment_trades_used()` — increments session trade counter post-send
- `_arm_update_session_pnl(delta)` — updates session P&L for loss-limit watcher
- `_auto_disarm_watcher()` — daemon thread, checks every 30s

**Auto-disarm triggers (watcher):**
- (A) Arm session expired
- (B) Databento feed disconnected (when enabled)
- (C) Daily loss limit breached for any allowed instrument
- (D) Session loss limit exceeded
- (E) LRE error count ≥ 10

**Session limits (conservative defaults):**
- `ARM_DEFAULT_DURATION_MIN = 30` (max 120)
- `ARM_DEFAULT_MAX_TRADES = 3`
- `ARM_DEFAULT_MAX_CONTRACTS = 1`
- Contracts never exceed hard global per-asset ceiling
- Session limits may only tighten global limits, never loosen them

### 3.2 Flask Routes (app.py)

All routes are owner-only; authentication enforced at the Express `/api` edge (not in `OPEN_PATHS`).

| Route | Method | Description |
|---|---|---|
| `/execution/state` | GET | Current arm state (sanitized, no secrets) |
| `/execution/arm` | POST | Arm with exact phrase + preflight + rate-limit |
| `/execution/disarm` | POST | Immediate disarm, blocks new entries |
| `/execution/kill-switch` | POST | Safety lock (blocks until reset) |
| `/execution/reset-safety-lock` | POST | Clears kill switch after reconciliation |
| `/execution/audit-log` | GET | Last 50 arm-state-change records |

**ARM confirmation phrase:** `"ARM LIVE AUTO TRADING"` (exact, case-sensitive)

### 3.3 Pre-Transmission Arm Gates (app.py)

**Gate 1: `_maybe_auto_execute()`** — after the Databento health gate, before `_AUTO_EXEC_LOCK`. Only active when `execution_is_live(mode)`. Records to `_record_exec_attempt()`. Paper/manual mode is byte-identical.

**Gate 2: `_execute_trade_gateway_inner()`** — final check immediately before `_send_broker_order()`. Only active when `execution_is_live(mode)`. Returns 409 with `reason_code` on failure. Paper/manual mode is byte-identical.

### 3.4 Express Proxy Whitelist (flask-proxy.ts)

Added `/execution/state`, `/execution/arm`, `/execution/disarm`, `/execution/kill-switch`, `/execution/reset-safety-lock`, `/execution/audit-log` to `BOT1_ROUTES`.

### 3.5 Dashboard Execution-Control Panel (app.py)

- `div#mod-armctl` (data-cat="advanced") — visible only when mode is live-capable
- Real-time state badge (ARMED/DISARMED/LOCKED/PAPER) updated every 30 seconds
- ARM button → confirmation modal with duration/max-trades/max-loss config + exact phrase
- DISARM button with native confirm dialog
- KILL SWITCH button with native confirm dialog
- RESET LOCK button (shown only when safety_locked)
- Status message strip (session ID, time remaining, trades used, mode)
- JavaScript: `refreshArmState()`, `showArmForm()`, `armExecution()`, `doDisarm()`, `doKillSwitch()`, `doResetSafetyLock()`

---

## 4. Safety Properties Verified

| Property | Mechanism |
|---|---|
| Always starts DISARMED | `_ARM_STATE` initialized with `armed=False` at module load |
| Disarm doesn't close positions | `_disarm()` never calls gateway/close functions |
| Emergency close is separate | `/execution/kill-switch` ≠ close; separate confirmation required |
| Kill switch overrides all states | `_effective_execution_state()` checks `safety_locked` first |
| No secret leakage in responses | `_record_arm_audit()` redacts password/token/webhook/secret keys |
| Rate limiting on arm attempts | Max 5 per 5 minutes per IP |
| Session limits ≤ global limits | Contract ceiling clamped to `max_contracts(inst)` in arm route |
| Fail-closed on exception | `_check_arm_for_transmission()` returns `(False, RC_DISARMED, {...})` on any exception |
| Paper mode byte-identical | Both gates gated on `execution_is_live(mode)` |
| Unknown protective-order state blocks rearm | `_reset_safety_lock()` checks `active_trade_for(inst).protective_order_state` |
| Arm session ID tracked | Mismatch returns `RC_ARM_SESSION_MISMATCH` |

---

## 5. Test Coverage

**Test file:** `test_auto_trading_arm_control.py`  
**Tests:** 74 total across 6 groups

| Group | Tests | Coverage |
|---|---|---|
| A: Startup & defaults | 10 | Initial state, mode mapping, disarm reason |
| B: Arming | 20 | Phrase validation, preflight checks, session params, rate-limit, audit |
| C: Transmission protection | 15 | All 9 arm-check gates, exception fail-closed, direction restriction |
| D: Automatic disarming | 15 | All watcher triggers, kill switch, effective state post-disarm |
| E: Concurrency | 5 | Simultaneous arm requests, 10-concurrent-order dedup, racing disarm/expiry |
| F: Existing positions | 9 | Disarm doesn't close, emergency close separate, lock-reset guards, audit log |

**Results:**
- 74/74 arm-control tests: ✅ PASS
- 164/174 original + remediation tests: same 10 pre-existing failures (not introduced by this work)
- SCALP GOLDEN: ✅ OK (byte-identical to baseline)
- PARITY: ✅ OK (registry/resolver identical)
- DUAL-SIM: ✅ OK
- BREAKOUT MODE: ✅ OK
- Flask syntax: ✅ OK

---

## 6. What Did NOT Change

Per spec §1 / SAFE FOR LIMITED LIVE PILOT constraints:
- Strategy logic, scoring, gates, risk formulas — **unchanged**
- Position sizing, TradersPost payload calculations — **unchanged**
- Paper/manual_only paths — **byte-identical** (gated on `execution_is_live()`)
- Existing money-path invariants — **preserved**
- Live execution remains disabled (EXECUTION_MODE is not changed by this work)

---

## 7. Operational Notes

1. **First-time setup:** Set `EXECUTION_MODE=traderspost` in env, then use the dashboard ARM button
2. **Monitoring:** `/api/execution/state` polls every 30s on the dashboard
3. **Audit trail:** `/api/execution/audit-log` returns last 50 state changes
4. **Session duration:** Default 30min, max 120min. Operator must explicitly re-arm after expiry
5. **Kill switch recovery:** POST `/execution/reset-safety-lock` — verifies no unknown position state first
6. **Existing positions on disarm:** Protective stops continue normally. Only new entries are blocked
