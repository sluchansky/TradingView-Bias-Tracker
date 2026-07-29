# PHASE_5_EXECUTION_BRIEF.md
# V1 Phase 5 — Manager and Execution Safety
# DOCUMENTATION ONLY — DO NOT MODIFY PRODUCTION CODE — DO NOT DEPLOY

---

## 1. Executive Summary

Phase 5 title (from IMPLEMENTATION_ROADMAP_V1.md §4): **Manager and Execution Safety**

Phase 5 is exclusively a **test-writing phase**. It does not authorize any changes to production code, execution behavior, gateway logic, safety controls, broker payloads, or dashboard HTML/CSS/JS. Every Phase 5 task creates a new test that proves existing behavior is correct.

Eight tasks (V1-P5-001 through V1-P5-008) cover Stream E (Manager and Execution Gateway):
- Arm-state boot reset (V1-P5-001)
- Entry-pending representation (V1-P5-002)
- Duplicate execution prevention (V1-P5-003) — BLOCKER priority
- Broker rejection handling (V1-P5-004)
- Execution timeout handling (V1-P5-005)
- Payload validation (V1-P5-006)
- Safe disarm behavior (V1-P5-007)
- Paper mode end-to-end (V1-P5-008)

**One critical research question blocks implementation order**: the roadmap and acceptance criteria reference `gateway_result.outcome` but the production implementation uses `gateway_result.status`. The tests must verify what the code actually produces, not what the ARCH document labels it. This is documented as RQ-1.

**One implementation gap found**: `ENTRY_PENDING` is not a named state or a field in `execute_trade_gateway()`'s return dict. The gateway is synchronous and returns either a success result (`"sent"` / `"simulated"`) or an error result. V1-P5-002 must document the actual entry lifecycle, not assume an ENTRY_PENDING field exists. This is documented as RQ-2.

**Recommendation: Ready to implement with documented RQ resolutions.** No production code changes are needed — all 8 tasks are test creation.

---

## 2. Baseline State

| Field | Value |
|---|---|
| **Branch** | `polish-v1` |
| **HEAD at start** | `e373220` — Add phase 4 controlled implementation operator explanation |
| **Accepted Phase 4 baseline** | `c9f55f9` — V1-P4 Operator Explanation and Decision Timeline |
| **Accepted Phase 4 is ancestor of HEAD** | YES — confirmed via `git merge-base --is-ancestor c9f55f9 HEAD` |
| **Working tree status** | Clean (only untracked: new instruction attachment) |
| **Tracked modifications** | None |
| **git diff --stat** | (empty) |
| **git diff --check** | CLEAN |

---

## 3. Intervening Commit Audit

One commit between `c9f55f9` (accepted Phase 4 baseline) and `e373220` (HEAD at start):

| SHA | Message | Classification | Files changed |
|---|---|---|---|
| `e373220` | Add phase 4 controlled implementation operator explanation | **attached-asset** | `attached_assets/Pasted--V1-PHASE-4-CONTROLLED-IMPLEMENTATION-OPERATOR-EXPLANAT_1785363680682.txt` only (942 lines added) |

**Production code changed:** None.
**Test files changed:** None.
**Overlap with Phase 5 scope:** None.

This commit is an attached instruction file added by the platform before Phase 4 implementation. It does not affect Phase 5 in any way and does not change the accepted Phase 4 baseline.

---

## 4. Controlling Document Review

Documents read and precedence applied:

| Rank | Document | Phase 5 references |
|---|---|---|
| 1 | `SYSTEM_ARCHITECTURE_V1.md` | Acceptance criteria AC-1.4, AC-5.1, AC-5.2, AC-5.3, AC-5.4, AC-5.5 (§8) |
| 2 | `IMPLEMENTATION_ROADMAP_V1.md` | Phase 5 section (§4, line 1077), Stream E section (§3, line 712), V1-P5-003 task card (§7, line 1359), priority table (§8, line 1459) |
| 3 | `PRODUCT_SPEC_V1.md` | Auto-trade arming/disarming (line 597, 635, 903) |
| 4 | `PLATFORM_BLUEPRINT.md` | Phase 5: Automation section (line 1980) — lists all automation features as COMPLETED |
| 5 | `PHASE_4_EXECUTION_BRIEF.md` | N/A to Phase 5 |
| 6 | `V1_PHASE_4_VALIDATION.md` | Confirms Phase 4 baseline; 57/57 tests passing at `c9f55f9` |
| 7 | `V1_PHASE_3_VALIDATION.md` | 60/60 passing |
| 8 | `V1_PHASE_2_MARKET_DATA_RELIABILITY_VALIDATION.md` | 45/45 passing |
| 9 | `V1_MANAGER_COACH_INTERFACE_VALIDATION.md` | Manager Interface v1 confirmed at `build_manager_interface()` — 9 fields, `_version: "v1"` |
| 10 | `V1_FIRST_BATCH_VALIDATION.md` | Expert interface v1 confirmed; Execution Gateway `_version: "v1"` added |

**Conflicts found:** None. `PRODUCT_SPEC_V1.md` Phase numbers differ from the implementation roadmap (product-feature phases vs. implementation-task phases) but this is documented as a known naming difference, not a behavioral conflict.

**Authoritative acceptance criteria for Phase 5 (from SYSTEM_ARCHITECTURE_V1.md §8 Category 5):**

| AC | Criterion | Pass condition |
|---|---|---|
| AC-1.4 | Auto-trade arm resets on boot | All instrument arm states initialize to False (OFF) regardless of previous session state |
| AC-5.1 | Execution gateway functions | In paper mode, sending an ENTER request produces `gateway_result` with no HTTP call to the broker |
| AC-5.2 | No duplicate executions | Sending the same READY setup signal twice does not produce two broker calls; `AUTO_FIRED_KEYS` prevents re-entry |
| AC-5.3 | Broker payload validation fires | A canonical intent missing a required field is rejected locally before any HTTP call |
| AC-5.4 | Training Mode suppresses execution at stage < 4 | `TRAINING_MODE_ENABLED=1` + stage 1-3 → no broker call; stage 4 passes through |
| AC-5.5 | Safety kill switch blocks execution | With kill switch active for an instrument, all execution attempts for that instrument are blocked |

---

## 5. Exact Phase 5 Title and Scope

**Title** (from IMPLEMENTATION_ROADMAP_V1.md §4, line 1077): **Phase 5 — Manager and Execution Safety**

**Goal** (verbatim): "Verify arm-state lifecycle, duplicate prevention, risk controls, broker routing, rejection handling, and safe disarm."

**Stream:** E — Manager and Execution Gateway

**Must not change** (verbatim): "Execution gateway behavior, safety controls, broker payload"

**Exit criteria** (verbatim):
- Duplicate-execution test passes (acceptance criterion 5.2)
- Broker-rejection test passes
- Payload-validation test passes (acceptance criterion 5.3)
- Boot-reset test passes (acceptance criterion 1.4)
- Paper mode E2E passes (acceptance criterion 5.1)
- All 4 primary regressions pass
- No execution behavior changed

**Eight tasks:**
1. V1-P5-001: Write arm-state boot-reset test
2. V1-P5-002: Write ENTRY_PENDING representation test
3. V1-P5-003: Write duplicate-execution test — BLOCKER priority
4. V1-P5-004: Write broker-rejection test
5. V1-P5-005: Write execution-timeout test
6. V1-P5-006: Write payload-validation test
7. V1-P5-007: Verify safe-disarm behavior
8. V1-P5-008: Verify paper mode end-to-end

---

## 6. Complete Task Inventory

### V1-P5-001: Arm-State Boot-Reset Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-001 |
| **Exact title** | Write arm-state boot-reset test (verify arm=False after simulated restart) |
| **Exact requirement** | Verify arm state resets to OFF on boot. Document boot reset as intentional safety. |
| **Business purpose** | Prevent phantom auto-execution on restart — a restart must not auto-fire pending trades from a prior session |
| **Architecture owner** | Manager (auto-trade arm state) |
| **Current status** | **NEEDS VERIFICATION** — behavior exists (line 1872: `AUTO_TRADE = {inst: False for inst in enabled_instruments()}`), no test |
| **Authoritative production functions** | `auto_trade_enabled(inst)` — reads `AUTO_TRADE[inst]` under `AUTO_TRADE_LOCK`; initialized at module load |
| **Callers** | `execute_trade_gateway()`, `_maybe_auto_execute()`, `build_manager_interface()` |
| **Runtime boundaries** | Read-only test; simulates module reload or reset of `AUTO_TRADE` dict |
| **Expected files to change** | New `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not change `AUTO_TRADE` initialization logic or `auto_trade_enabled()` |
| **Prerequisites** | Phase 4 baseline green |
| **Required tests** | Assert: after module import, `auto_trade_enabled(inst)` is `False` for all instruments; assert: after programmatically setting to `True`, a simulated reset returns it to `False` |
| **Completion criteria** | Test passes; all 4 primary regressions still pass |
| **Implementation risk** | LOW |
| **Deployment relevance** | None |
| **Unresolved questions** | None |

---

### V1-P5-002: ENTRY_PENDING Representation Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-002 |
| **Exact title** | Write ENTRY_PENDING representation test (gateway_result fields present after execute attempt) |
| **Exact requirement** | Define how ENTRY_PENDING is represented and how it transitions to ACTIVE TRADE or back to READY on failure. |
| **Business purpose** | Operator must know what state the system is in between issuing an order and receiving broker confirmation |
| **Architecture owner** | Manager / Execution Gateway |
| **Current status** | **NEEDS VERIFICATION — IMPLEMENTATION GAP** — `execute_trade_gateway()` is synchronous and returns either success or error immediately. There is no `ENTRY_PENDING` state or field in any current result dict. See RQ-2. |
| **Authoritative production functions** | `execute_trade_gateway(instrument, contracts, source, direction, expected_stop)` — synchronous; returns `(result_dict, http_code)` |
| **Result dict fields** | `status` (string), `provider`, `mode`, `broker_verify_required` (bool), `message`, `plan` (dict), `_version: "v1"` |
| **Status values observed** | `"manual_required"`, `"simulated"`, `"sent"`, `"error"` |
| **"ENTRY_PENDING" interpretation** | The window between gateway call start and return is the entry-pending window; since the gateway is synchronous, the caller blocks during this period. No explicit field named `ENTRY_PENDING` exists. |
| **Transition on success** | `status == "sent"` → caller sets `ACTIVE_TRADES_BY_INST[inst]`; `status == "simulated"` → caller sets `ACTIVE_TRADES_BY_INST[inst]` or MANAGED_TRADES lifecycle |
| **Transition on failure** | `status == "error"` → `ACTIVE_TRADES_BY_INST[inst]` NOT set; setup remains READY |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not add an ENTRY_PENDING field or change gateway sync/async behavior |
| **Prerequisites** | V1-P5-001 |
| **Required tests** | (a) Paper mode: assert `status == "simulated"` and `plan` dict present after call; (b) Error path: assert `status == "error"` when instrument not in ASSETS; (c) Confirm no `ACTIVE_TRADE` set when `status == "error"` |
| **Completion criteria** | Test passes; gateway_result fields documented via test |
| **Implementation risk** | LOW |
| **Deployment relevance** | None |
| **Unresolved questions** | RQ-2 (ENTRY_PENDING field): resolved — no explicit field; test documents the synchronous model |

---

### V1-P5-003: Duplicate Execution Prevention Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-003 |
| **Exact title** | Write dedicated duplicate-execution prevention test |
| **Exact requirement** (verbatim from roadmap) | "send same setup signal twice → verify one order attempt, second suppressed by AUTO_FIRED_KEYS" |
| **Source requirement** | ARCH §8 AC-5.2; ARCH §5 Scenario 5; Implementation Principle 8 |
| **Business purpose** | No duplicate broker orders for the same setup; prevents doubling into a position by accident |
| **Architecture owner** | Manager / Execution Gateway |
| **Priority** | **BLOCKER** (score 37.5 — highest priority test in Phase 5) |
| **Current status** | **MISSING** — `AUTO_FIRED_KEYS` dedup store exists at line 1892; `_maybe_auto_execute` and `_do_auto_execute_webhook` both check `setup_key in AUTO_FIRED_KEYS` before firing; no dedicated test sends the same signal twice |
| **Authoritative production functions** | `AUTO_FIRED_KEYS` (set, line 1892); `_maybe_auto_execute()`; `_do_auto_execute_webhook()` (line 36898); `execute_trade_gateway()` (paper mode only for test) |
| **Callers** | Webhook handler `→ _do_auto_execute_webhook()` or `_maybe_auto_execute()` |
| **Runtime boundaries** | Paper mode only; no live broker HTTP call; no real position risk |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify `AUTO_FIRED_KEYS` logic or `_maybe_auto_execute()` |
| **Prerequisites** | V1-P5-002 |
| **Required tests** | In paper mode with `auto_trade_enabled(inst) == True`: (1) send READY signal → assert gateway fires (status == "simulated"); (2) send identical signal with same key → assert `AUTO_FIRED_KEYS` contains the key → assert no second gateway call; (3) assert exactly one paper result |
| **Completion criteria** | Test passes; dedup suppression demonstrated by log or `AUTO_FIRED_KEYS` membership assertion |
| **Implementation risk** | LOW (test-only, paper mode) |
| **Deployment relevance** | None |
| **Unresolved questions** | None — dedup key composition is `(instrument, direction, zone_low)` composite; setup_key must be constructed consistently |

---

### V1-P5-004: Broker Rejection Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-004 |
| **Exact title** | Write broker-rejection test (mock non-2xx broker response → no ACTIVE_TRADE set) |
| **Exact requirement** | "mock non-2xx broker response → no ACTIVE_TRADE set → gateway_result.outcome='broker_rejected'" |
| **Source requirement** | ARCH §8 AC-5.2; Stream E broker rejection handling |
| **Business purpose** | Confirm a rejected order at the broker does not create a phantom active trade |
| **Architecture owner** | Execution Gateway (`_send_broker_order`) |
| **Current status** | **NEEDS VERIFICATION** — `_send_broker_order()` handles 4xx: releases slot + returns `{"status": "error", "reason": "...rejected..."}` with HTTP 502; `_maybe_auto_execute()` checks `status in ("sent", "simulated")` and does NOT set ACTIVE_TRADE on error; no dedicated test |
| **Authoritative production functions** | `_send_broker_order()` (line 46069); `_maybe_auto_execute()` (line 48615); `ACTIVE_TRADES_BY_INST` |
| **Key finding** | ARCH says `gateway_result.outcome="broker_rejected"` but production uses `gateway_result.status="error"` with a `reason` string containing "rejected". Tests must verify `status == "error"` and `broker_verify_required` is absent (4xx releases slot). See RQ-1. |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify `_send_broker_order()` or change its return structure |
| **Prerequisites** | V1-P5-002 |
| **Required tests** | Mock `requests.post` to return HTTP 400; call `execute_trade_gateway()` in traderspost mode; assert (a) `status == "error"` in result, (b) `broker_verify_required` is falsy or absent, (c) `ACTIVE_TRADES_BY_INST[inst]` is NOT set, (d) no active trade in `active_trade_for(inst)` after call |
| **Completion criteria** | Test passes; ACTIVE_TRADE isolation on rejection verified |
| **Implementation risk** | LOW (mock only; paper assertion) |
| **Deployment relevance** | None |
| **Unresolved questions** | RQ-1 (status vs outcome field name): resolved — tests use `status` not `outcome` |

---

### V1-P5-005: Execution Timeout Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-005 |
| **Exact title** | Write execution-timeout test (mock timeout → no ACTIVE_TRADE set → gateway_result.outcome="timeout") |
| **Exact requirement** | "mock timeout → no ACTIVE_TRADE set → gateway_result.outcome='timeout'" |
| **Business purpose** | Confirm a timed-out order does not create a phantom ACTIVE_TRADE; confirms broker slot held for safety |
| **Architecture owner** | Execution Gateway (`_send_broker_order`) |
| **Current status** | **NEEDS VERIFICATION** — `_send_broker_order()` handles `requests.RequestException` (which includes `Timeout`): returns `{"status": "error", "broker_verify_required": True, "reason": "...did not confirm..."}` with HTTP 502 and HOLDS the dedup slot (cannot retry); no dedicated test |
| **Authoritative production functions** | `_send_broker_order()` (line 46142: `except requests.RequestException`); `requests.exceptions.Timeout` is a subclass of `RequestException` |
| **Key finding** | ARCH says `gateway_result.outcome="timeout"` but production uses `gateway_result.status="error"` with `broker_verify_required=True`. The timeout and 5xx paths are both "ambiguous" (slot held). Tests must verify `status == "error"` and `broker_verify_required == True`. See RQ-1. |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify `_send_broker_order()` exception handling |
| **Prerequisites** | V1-P5-002 |
| **Required tests** | Mock `requests.post` to raise `requests.exceptions.Timeout`; call `execute_trade_gateway()` in traderspost mode; assert (a) `status == "error"`, (b) `broker_verify_required == True`, (c) `active_trade_for(inst)` is None, (d) dedup slot is held (key still in `_TRADERSPOST_LAST`) |
| **Completion criteria** | Test passes; ACTIVE_TRADE isolation on timeout verified; slot-held behavior documented |
| **Implementation risk** | LOW (mock only) |
| **Deployment relevance** | None |
| **Unresolved questions** | RQ-1 resolved — use `status` |

---

### V1-P5-006: Payload Validation Test

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-006 |
| **Exact title** | Write payload-validation test (missing required field → gateway_result.outcome="invalid_payload") |
| **Exact requirement** | "missing required field → gateway_result.outcome='invalid_payload'" |
| **Source requirement** | ARCH §8 AC-5.3 |
| **Business purpose** | Confirm a malformed order intent is caught locally before any HTTP call to the broker |
| **Architecture owner** | Execution Gateway (`_send_broker_order` validation; `_validate_broker_payload`) |
| **Current status** | **NEEDS VERIFICATION** — `_send_broker_order()` calls `_validate_broker_payload(mode, payload)` at line ~46100; if bad fields found, returns `{"status": "error", "blocked_fields": [...], "reason": "..."}` with HTTP 400 and RELEASES slot; `_broker_required_fields(mode)` defines required fields per mode; no dedicated test |
| **Authoritative production functions** | `_validate_broker_payload()` (called inside `_send_broker_order`); `_broker_required_fields(mode)` (line 2202); TradersPost requires `ticker` + `action`; PickMyTrade requires `symbol` + `data` |
| **Key finding** | ARCH says `gateway_result.outcome="invalid_payload"` but production uses `gateway_result.status="error"` with `blocked_fields` list. Tests must verify `status == "error"` and `blocked_fields` present. See RQ-1. |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify `_validate_broker_payload()` or `_broker_required_fields()` |
| **Prerequisites** | V1-P5-002 |
| **Required tests** | Set EXECUTION_MODE=traderspost; provide a malformed intent missing `ticker`; assert (a) `status == "error"` in result, (b) `blocked_fields` is a non-empty list, (c) no HTTP call to broker was made (mock `requests.post` and assert it was NOT called), (d) dedup slot released |
| **Completion criteria** | Test passes; local-rejection-before-send confirmed |
| **Implementation risk** | LOW (mock-only test) |
| **Deployment relevance** | None |
| **Unresolved questions** | RQ-1 resolved — use `status` + `blocked_fields` |

---

### V1-P5-007: Safe Disarm Behavior

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-007 |
| **Exact title** | Verify safe-disarm behavior (disarm → arm=False → no close of existing trade) |
| **Exact requirement** | "Verify disarm clears arm state but does not close an open trade." |
| **Business purpose** | Disarming auto-trade must not silently flatten the operator's live position |
| **Architecture owner** | Manager (auto-trade arm state + ACTIVE_TRADES_BY_INST) |
| **Current status** | **NEEDS VERIFICATION** — `/auto-trade` endpoint sets `AUTO_TRADE[inst] = False` (line 64191); `_maybe_auto_execute()` checks `auto_trade_enabled(inst)` before firing; no dedicated test verifying disarm does not trigger close |
| **Authoritative production functions** | `auto_trade_enabled(inst)` (line 2332); `AUTO_TRADE[inst] = False` in `/auto-trade` endpoint; `set_active_trade()` (line 172); `clear_active_trade()` (line 186) |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify disarm logic or active trade management |
| **Prerequisites** | V1-P5-001 |
| **Required tests** | (1) Set `AUTO_TRADE["MGC"] = True`; inject a synthetic active trade via `set_active_trade("MGC", trade)`; (2) Disarm by setting `AUTO_TRADE["MGC"] = False`; (3) Assert `auto_trade_enabled("MGC") == False`; (4) Assert `active_trade_for("MGC")` is still the trade (not None); (5) Assert `clear_active_trade()` was NOT called automatically |
| **Completion criteria** | Test passes; disarm-does-not-close invariant documented |
| **Implementation risk** | LOW |
| **Deployment relevance** | None |
| **Unresolved questions** | None |

---

### V1-P5-008: Paper Mode End-to-End

| Field | Detail |
|---|---|
| **Task ID** | V1-P5-008 |
| **Exact title** | Verify paper mode end-to-end (READY → auto-fire → paper log → no broker HTTP call) |
| **Exact requirement** | "READY → auto-fire → paper log → no broker HTTP call" |
| **Source requirement** | ARCH §8 AC-5.1; Stream E paper mode |
| **Business purpose** | Confirm the paper-mode safety path: simulated execution with full lifecycle tracking but no real money at risk |
| **Architecture owner** | Execution Gateway + Manager |
| **Priority** | HIGH (score 36.0) |
| **Current status** | **NEEDS VERIFICATION** — paper mode returns `{"status": "simulated", ...}` at line 48489 with no `requests.post` call to broker; Discord notification is attempted (not required for test); no dedicated E2E test |
| **Authoritative production functions** | `execute_trade_gateway()` with `mode == "paper"` (line 48489); Discord send is best-effort (ignored on failure); result includes `plan` dict |
| **Expected files to change** | `artifacts/tradingview-webhook/test_phase5_execution_safety.py` |
| **Interfaces affected** | None |
| **Authorized behavior changes** | None |
| **Prohibited behavior changes** | Must not modify paper mode path; must not introduce actual HTTP calls |
| **Prerequisites** | V1-P5-003 |
| **Required tests** | Set `EXECUTION_MODE=paper`; mock `requests.post` and assert NOT called (or called only for Discord, not for a broker URL); call `execute_trade_gateway("MGC", 1, source="test")`; assert (a) `status == "simulated"`, (b) `plan` dict present with entry/stop/takeProfit keys, (c) no call to `TRADERSPOST_WEBHOOK_URL` or `EXECUTION_WEBHOOK_URL` |
| **Completion criteria** | Test passes; paper isolation from broker confirmed; plan dict fields verified |
| **Implementation risk** | LOW |
| **Deployment relevance** | None |
| **Unresolved questions** | None |

---

## 7. Existing Implementation Audit

### AUTO_TRADE Boot Reset (V1-P5-001)

| Item | Detail |
|---|---|
| **File** | `artifacts/tradingview-webhook/app.py` |
| **Function/variable** | `AUTO_TRADE` dict (line 1872) |
| **Line range** | 1872–1873 |
| **Code** | `AUTO_TRADE = {inst: False for inst in enabled_instruments()}` |
| **Canonical owner** | Manager |
| **Failure behavior** | N/A — always runs at module load; a failed `enabled_instruments()` call would fail the import, not silently set True |
| **Persistence** | Intentionally NOT persisted (design decision in `auto-trade-arming-lifecycle.md` memory) |
| **Tests** | None |
| **V1-P5-001 status** | Behavior COMPLETE; test MISSING |

### Execution Gateway — All Status Paths

| File | Function | Lines | Owner | Status values |
|---|---|---|---|---|
| `app.py` | `execute_trade_gateway()` | 47872–48610 | Execution Gateway | `"manual_required"`, `"simulated"`, `"sent"`, `"error"` |
| `app.py` | `_send_broker_order()` | 46069–46160 | Execution Gateway | Returns `(None, None)` on 2xx; `({"status":"error",...}, 502)` on rejection/timeout/exception; `({"status":"error","blocked_fields":[...]}, 400)` on validation failure |

**Key finding — ARCH field-name discrepancy:**

The ARCH acceptance criteria use `gateway_result.outcome` (e.g., `outcome: "broker_rejected"`, `outcome: "timeout"`, `outcome: "invalid_payload"`). The production implementation uses `gateway_result.status` with the values `"error"`, `"sent"`, `"simulated"`, `"manual_required"`. There is no `outcome` field and no values named `broker_rejected`, `timeout`, or `invalid_payload`.

| ARCH uses | Implementation uses | Additional discriminator |
|---|---|---|
| `outcome: "broker_rejected"` | `status: "error"` | `reason` contains "rejected"; `broker_verify_required` absent or False; HTTP 502; slot RELEASED |
| `outcome: "timeout"` | `status: "error"` | `broker_verify_required: True`; HTTP 502; slot HELD |
| `outcome: "invalid_payload"` | `status: "error"` | `blocked_fields: [list]`; HTTP 400; slot RELEASED; NO broker HTTP call made |
| `outcome: "paper"` | `status: "simulated"` | `mode: "paper"`; no broker HTTP call |
| `outcome: "sent"` | `status: "sent"` | 2xx from broker |

**Tests must use the implementation field names** (`status`, `broker_verify_required`, `blocked_fields`), not the ARCH terminology.

### AUTO_FIRED_KEYS Dedup

| File | Variable/function | Lines |
|---|---|---|
| `app.py` | `AUTO_FIRED_KEYS = set()` | 1892 |
| `app.py` | Check in `_maybe_auto_execute()` | 25986, 26243 |
| `app.py` | Check in `_do_auto_execute_webhook()` | 36901 |
| `app.py` | Persist after add | ~33368 (within `_save_market_state`) |
| `app.py` | Restore on boot | ~33322 (within `_restore_market_state`) |

**Note:** `AUTO_FIRED_KEYS` IS persisted (via `market_state_cache` table) across restarts for the same trading day, unlike `AUTO_TRADE` which resets. Restoration is gated on a freshness check (stale date = skip). This is important for V1-P5-003: the test must clear `AUTO_FIRED_KEYS` before the first send to avoid contamination from any prior test run.

### ENTRY_PENDING Gap

`ENTRY_PENDING` does not exist as:
- A field name in any gateway return dict
- A named platform state in `platform_state()` (that function is also TODO per TD-004)
- An enum or string constant

The concept is implicit: the caller of `execute_trade_gateway()` blocks synchronously until the function returns. During that blocking period the entry is "pending." V1-P5-002 must document this as the actual model, not assume an explicit ENTRY_PENDING field.

### Paper Mode Path

| Location | Lines | Behavior |
|---|---|---|
| `execute_trade_gateway()` | 48489–48510 | `mode == "paper"` → logs, attempts Discord best-effort, returns `{"status": "simulated", "plan": plan_public, "_version": "v1"}`, HTTP 200; no broker URL called |
| Broker URL | Not called | `requests.post(send_url, ...)` is ONLY reached after the paper branch returns |

### Disarm Path

| Location | Lines | Behavior |
|---|---|---|
| `/auto-trade` endpoint | ~64191 | Sets `AUTO_TRADE[inst] = enabled`; does not call `clear_active_trade()` |
| `_maybe_auto_execute()` | ~48636 | Returns `False` if `not auto_trade_enabled(inst)` — skips new trade; does not affect existing |
| ACTIVE_TRADES_BY_INST | Independent | Holds the live position regardless of arm state; only `clear_active_trade()` clears it |

### Test Coverage Gap (confirmed)

No Phase 5 test files exist:
- `artifacts/tradingview-webhook/test_phase5*.py` — NOT FOUND
- `.local/state/check_arm*.sh` — NOT FOUND
- `.local/state/check_duplicate*.sh` — NOT FOUND
- `.local/state/check_paper*.sh` — NOT FOUND
- `.local/state/check_gateway*.sh` — NOT FOUND

Only related existing test: `.local/state/check_broker_send.sh` (T3 smoke for `_send_broker_order` refactor byte-identity) — covers 5 outcomes of `_send_broker_order` in isolation but does NOT test the full `execute_trade_gateway()` lifecycle, arm state, dedup, or paper E2E.

---

## 8. Dependency Graph

```
Phase 1 (V1-P1-005) — Execution Gateway v1 _version field
    └── V1-P5-001: Arm-state boot reset
            └── V1-P5-007: Safe disarm (depends on arm-control concepts)

Phase 4 (V1-P4 complete)
    └── V1-P5-002: ENTRY_PENDING representation (gateway_result contract)
            ├── V1-P5-003: Duplicate execution prevention  [BLOCKER]
            │       └── V1-P5-008: Paper mode E2E (depends on clean dedup state)
            ├── V1-P5-004: Broker rejection
            ├── V1-P5-005: Execution timeout
            └── V1-P5-006: Payload validation
```

**Roadmap explicit dependency chain:**
- V1-P5-003 depends on V1-P5-002 AND V1-P1-005 (gateway version field — already done)
- V1-P5-004 and V1-P5-005 depend on V1-P5-002
- V1-P5-007 depends on V1-P5-001
- V1-P5-008 depends on V1-P5-003

**Can run in parallel:** V1-P5-001 and V1-P5-002 are independent. After V1-P5-002 completes, V1-P5-004, V1-P5-005, and V1-P5-006 can be written together.

**Must be sequential:** V1-P5-003 before V1-P5-008; V1-P5-001 before V1-P5-007.

**External dependencies:**
- `check_broker_send.sh` already exercises `_send_broker_order` in isolation — Phase 5 tests add the full-gateway lifecycle layer on top
- `AUTO_FIRED_KEYS` restoration from `market_state_cache` means tests MUST clear the dedup set before Phase 5 tests run to avoid state bleed from prior runs

---

## 9. Research Questions

### RQ-1: ARCH uses `gateway_result.outcome`; implementation uses `gateway_result.status`

| Field | Detail |
|---|---|
| **Question** | Should Phase 5 tests assert `gateway_result.outcome == "broker_rejected"` (as ARCH AC-5.3 implies) or `gateway_result.status == "error"` (as the code produces)? |
| **Why it matters** | Tests asserting a non-existent field will always fail; tests asserting the wrong semantics will pass even if the behavior is wrong |
| **Likely owner** | Execution Gateway |
| **Evidence available** | Code: `_send_broker_order()` returns `{"status": "error", ...}`. ARCH: says `outcome`. `_maybe_auto_execute()` reads `result.get("status")` not `result.get("outcome")`. |
| **Evidence still required** | None — code is authoritative |
| **Blocks implementation** | YES — tests must be written against the actual field name |
| **Resolution** | **USE `status`, NOT `outcome`.** Tests must assert `result.get("status") == "error"` and use supplementary fields (`blocked_fields`, `broker_verify_required`) as discriminators. The ARCH wording `outcome: "broker_rejected"` is a conceptual label, not a literal field. |

---

### RQ-2: ENTRY_PENDING field does not exist in the gateway

| Field | Detail |
|---|---|
| **Question** | Is `ENTRY_PENDING` a real field in the gateway result that should be tested, or a conceptual state? |
| **Why it matters** | V1-P5-002 is titled "ENTRY_PENDING representation test" — if there's no field, the test may be testing the wrong thing |
| **Likely owner** | Execution Gateway |
| **Evidence available** | Codebase search for `ENTRY_PENDING`, `entry_pending`, `gateway_result`: all return no matches in app.py |
| **Evidence still required** | None — absence confirmed |
| **Blocks implementation** | YES — V1-P5-002 must document the actual model |
| **Resolution** | **Document the synchronous model.** `execute_trade_gateway()` is synchronous. The "pending" period is the blocking call duration. V1-P5-002 must test that `gateway_result` fields (`status`, `plan`, `provider`, `mode`, `_version`) are present and correct for every path — success and failure. The test documents the contract, not a non-existent state. |

---

### RQ-3: Roadmap says smoke scripts (`.local/state/check_*.sh`); Phases 3-4 used Python pytest files

| Field | Detail |
|---|---|
| **Question** | Should Phase 5 tests be shell scripts (`.local/state/check_*.sh`) or Python pytest files (`test_phase5_execution_safety.py`) as per the Phases 2-4 pattern? |
| **Why it matters** | File location determines whether tests are in the regression command set, how they're organized, and how they're invoked |
| **Likely owner** | Phase 5 implementation decision |
| **Evidence available** | Roadmap task card V1-P5-003 says "Files likely involved: New `.local/state/check_duplicate_execution.sh`". Phases 3 and 4 used `test_phase3_*.py` / `test_phase4_*.py` style. Phase 2 used both (smoke shell + pytest). |
| **Evidence still required** | None — either is valid |
| **Blocks implementation** | YES — file location determines regression commands |
| **Resolution** | **Use the Phase 2-4 precedent: one Python pytest file `test_phase5_execution_safety.py`** under `artifacts/tradingview-webhook/`. This file can be invoked by a wrapping shell script if a smoke-script form is also wanted. The roadmap's `.local/state/check_*.sh` suggestion was written before the Phase 2-4 pattern was established. A Python pytest file integrates cleanly with the existing regression command `python3 -m pytest test_phase5_execution_safety.py`. The V1-P5-003 task card says "The smoke script IS the test" — this is a format preference, not an architectural requirement; functional equivalence is preserved with a pytest file. |

---

### RQ-4: `AUTO_FIRED_KEYS` test isolation

| Field | Detail |
|---|---|
| **Question** | How should V1-P5-003 handle the `AUTO_FIRED_KEYS` global set between test runs — it is populated by the restore path from `market_state_cache`? |
| **Why it matters** | A prior test run or a cached restore could pre-populate `AUTO_FIRED_KEYS`, causing a fresh first-send to look like a duplicate |
| **Likely owner** | V1-P5-003 test setup |
| **Resolution** | **Test setup must explicitly clear `AUTO_FIRED_KEYS` before each dedup test** (via `app.AUTO_FIRED_KEYS.clear()`) and restore it in `finally`. Also: arm state (`AUTO_TRADE`) must be set to `True` for the test instrument and restored to `False` in `finally`. |

---

## 10. File Impact Matrix

### Production Code

| File | Owning subsystem | Authorized to change? | Why |
|---|---|---|---|
| `artifacts/tradingview-webhook/app.py` | All | **NO** | Phase 5 is test-writing only |
| `artifacts/tradingview-webhook/left_brain_market_intelligence.py` | Left Brain | **NO** | Not in Phase 5 scope |

### Tests

| File | Owning subsystem | Task IDs | Expected content | Size estimate |
|---|---|---|---|---|
| `artifacts/tradingview-webhook/test_phase5_execution_safety.py` | Manager / Gateway | V1-P5-001..008 | 8 test groups, ~40-60 test checks | NEW, ~600-800 lines |

### Validation Documentation

| File | Task IDs | Content |
|---|---|---|
| `V1_PHASE_5_VALIDATION.md` | All | Post-implementation validation record (created after tests pass) |

### Migration / Schema Files

None — Phase 5 creates no new database tables and modifies no schema.

### Configuration

None — no new environment variables needed (tests use `os.environ.setdefault()` for required vars).

### Smoke Scripts

Optionally: `.local/state/check_phase5_smoke.sh` — a thin wrapper that calls `python3 artifacts/tradingview-webhook/test_phase5_execution_safety.py` (parallel to Phase 2 pattern). Not required if pytest invocation is used directly.

### Important Files NOT Changing (with reason)

| File | Reason for no change |
|---|---|
| `app.py` | Phase 5 is test-only; production behavior must remain byte-identical |
| `.local/state/check_parity.sh` | Golden behavior unchanged |
| `.local/state/check_scalp_golden.sh` | Golden behavior unchanged |
| `.local/state/check_dual_sim.sh` | Gateway not called by these tests |
| `.local/state/check_breakout_mode.sh` | Gateway not called by these tests |
| `.local/state/broker_send_smoke.py` | Existing T3 smoke; not superseded — tests different layer |
| `artifacts/tradingview-webhook/test_phase4_operator_explanation.py` | Phase 4 test; not modified |

---

## 11. Canonical Interface Impact Matrix

| Interface | Version | Phase 5 effect | Reason |
|---|---|---|---|
| **Left Brain v2** | `_version: "v2"` | **Unchanged** | Not called in execution path |
| **Expert v1** | `_version: "v1"` | **Unchanged** | Tests call `full_analysis()` read-only at most |
| **Partner v1** | `_version: "v1"` | **Unchanged** | Not part of execution path |
| **Manager v1** | `_version: "v1"` | **Unchanged** (additive read) | `build_manager_interface()` may be called read-only in V1-P5-007 to verify disarm; no write |
| **Execution Gateway v1** | `_version: "v1"` | **Unchanged** | Tests call in paper/mock mode; no schema changes |
| **Journal v1** | `_version: "v1"` | **Unchanged** | Not tested in Phase 5 |
| **Coach v1** | `_version: "v1"` | **Unchanged** | Not tested in Phase 5 |

**No interface version changes required.** No required fields added, removed, or renamed. No `_version` bump needed.

---

## 12. Behavioral Change Matrix

| Behavior | Change authorized? | Controlling requirement |
|---|---|---|
| Verdict (READY/WAIT) | **Must remain unchanged** | "Must not change: Execution gateway behavior" |
| Direction | **Must remain unchanged** | Not in Phase 5 scope |
| Confidence | **Must remain unchanged** | Not in Phase 5 scope |
| Edge score | **Must remain unchanged** | Not in Phase 5 scope |
| Edge grade | **Must remain unchanged** | Not in Phase 5 scope |
| Actionability | **Must remain unchanged** | Not in Phase 5 scope |
| Readiness thresholds | **Must remain unchanged** | Not in Phase 5 scope |
| Failed conditions | **Must remain unchanged** | Not in Phase 5 scope |
| Veto behavior | **Must remain unchanged** | Not in Phase 5 scope |
| Strategy selection | **Must remain unchanged** | Not in Phase 5 scope |
| Strategy scanner universe | **Must remain unchanged** | Not in Phase 5 scope |
| Session gates | **Must remain unchanged** | Not in Phase 5 scope |
| Risk / sizing | **Must remain unchanged** | "Must not change: safety controls" |
| Stops / targets | **Must remain unchanged** | Not in Phase 5 scope |
| Active-trade management | **Must remain unchanged** | Tests are read-only + cleanup in `finally` |
| Journal persistence | **Must remain unchanged** | Not in Phase 5 scope |
| Learning weights | **Must remain unchanged** | Not in Phase 5 scope |
| Learning thresholds | **Must remain unchanged** | Not in Phase 5 scope |
| Database schema | **Must remain unchanged** | No DDL in tests |
| Databento behavior | **Must remain unchanged** | Not in Phase 5 scope |
| Broker routing | **Must remain unchanged** | "Must not change: broker payload" |
| TradersPost payloads | **Must remain unchanged** | "Must not change: broker payload" |
| Live execution authorization | **Must remain unchanged** | Tests use paper/mock mode only |
| Dashboard display | **Must remain unchanged** | Not in Phase 5 scope |
| Authentication | **Must remain unchanged** | Not in Phase 5 scope |
| Deployment configuration | **Must remain unchanged** | Phase 5 does not deploy |
| AUTO_FIRED_KEYS logic | **Must remain unchanged** | Tests verify existing behavior; must not fix or change |
| execute_trade_gateway() return schema | **Must remain unchanged** | Tests document; must not change production |

---

## 13. Validation Strategy

### Per-Task Test Requirements

| Task | Test group | Type | Normal case | Degraded case | Unavailable case | Malformed case | Side-effect assertion |
|---|---|---|---|---|---|---|---|
| V1-P5-001 | Arm-state boot reset | Unit | `auto_trade_enabled(inst) == False` at module load | — | — | — | `AUTO_TRADE` dict is per-instrument, not global |
| V1-P5-001 | Arm state set and verify | Unit | `AUTO_TRADE[inst] = True` → `auto_trade_enabled == True` | — | — | — | Does not affect other instruments |
| V1-P5-002 | Gateway result fields (paper) | Integration | `status == "simulated"`, `plan` present, `_version == "v1"` | — | Invalid instrument → `status == "error"` | No trade plan → `status == "error"` | No ACTIVE_TRADE on error |
| V1-P5-002 | Gateway result fields (manual) | Integration | `status == "manual_required"` when `EXECUTION_MODE=manual_only` | — | — | — | No broker HTTP call |
| V1-P5-003 | Dedup first fire | Integration | `status == "simulated"`, key added to `AUTO_FIRED_KEYS` | — | — | — | Key in set after first fire |
| V1-P5-003 | Dedup second fire | Integration | Second identical key → gateway NOT called again | — | — | — | `AUTO_FIRED_KEYS` unchanged after suppression |
| V1-P5-004 | Broker rejection | Integration (mocked) | `status == "error"`, `broker_verify_required` falsy, no ACTIVE_TRADE | — | — | — | Dedup slot released |
| V1-P5-005 | Execution timeout | Integration (mocked) | `status == "error"`, `broker_verify_required == True`, no ACTIVE_TRADE | — | — | — | Dedup slot held |
| V1-P5-006 | Payload validation | Integration (mocked) | `status == "error"`, `blocked_fields` present, no broker HTTP | — | — | Empty required field → same | `requests.post` not called |
| V1-P5-007 | Disarm does not close | Integration | `auto_trade_enabled == False` after disarm | — | — | — | `active_trade_for(inst)` unchanged |
| V1-P5-007 | Disarm clears arm only | Integration | `AUTO_TRADE[inst] == False`, `ACTIVE_TRADES_BY_INST` unmodified | — | — | — | `clear_active_trade` not called |
| V1-P5-008 | Paper E2E | Integration (mocked) | `status == "simulated"`, `plan` complete, broker HTTP not called | — | — | — | Discord send attempted but not required for pass |

### Fault-Injection Tests Required

- `requests.post` mock returning HTTP 400 (for V1-P5-004)
- `requests.exceptions.Timeout` raised from `requests.post` (for V1-P5-005)
- `requests.post` assert-not-called for paper mode and invalid payload (V1-P5-006, V1-P5-008)

### Non-Mutation Tests Required

- All tests must restore `AUTO_TRADE[inst]` to `False` in `finally` blocks
- All tests must restore injected `ACTIVE_TRADES_BY_INST` entries in `finally`
- V1-P5-003 must restore `AUTO_FIRED_KEYS` state in `finally`
- Tests must not leave `_TRADERSPOST_LAST` in a polluted state

### Golden Tests

All 4 golden tests (parity, scalp_golden, dual_sim, breakout_mode) must remain byte-identical after Phase 5. No golden changes are authorized.

### Node.js Syntax Check

Not required — dashboard HTML/CSS/JS is not modified in Phase 5.

---

## 14. Regression Contract

After Phase 5 implementation, the following must pass:

| Suite | Command | Minimum pass count | Notes |
|---|---|---|---|
| **Phase 5 (new)** | `python3 artifacts/tradingview-webhook/test_phase5_execution_safety.py` | All checks pass | New |
| Phase 4 | `python3 artifacts/tradingview-webhook/test_phase4_operator_explanation.py` | 57/57 | Must remain unchanged |
| Phase 3 | `python3 -m pytest artifacts/tradingview-webhook/test_phase3_thesis_verdict_pipeline.py` | 60/60 | Must remain unchanged |
| Interface tests | `python3 -m pytest artifacts/tradingview-webhook/test_v1_interface_versions.py` | 85/85 | Must remain unchanged |
| Phase 2 | `python3 -m pytest artifacts/tradingview-webhook/test_phase2_market_data_reliability.py` | 45/45 | Must remain unchanged |
| Phase 2 smoke (workspace) | `bash artifacts/tradingview-webhook/checks/run_phase2_smoke.sh` | 8/8 | Must remain unchanged |
| Phase 2 smoke (/tmp) | `bash /home/runner/workspace/artifacts/tradingview-webhook/checks/run_phase2_smoke.sh` | 8/8 | Must remain unchanged |
| **parity** | `bash .local/state/check_parity.sh` | PASS | BYTE-IDENTICAL required |
| **scalp_golden** | `bash .local/state/check_scalp_golden.sh` | PASS | BYTE-IDENTICAL required |
| **dual_sim** | `bash .local/state/check_dual_sim.sh` | PASS | Node-check included |
| **breakout_mode** | `bash .local/state/check_breakout_mode.sh` | PASS | Node-check included |
| Python syntax | `python3 -c "import py_compile; py_compile.compile('artifacts/tradingview-webhook/app.py', doraise=True)"` | OK | Must pass |
| git diff --check | `git diff --check` | CLEAN | No trailing whitespace |

**Outputs that must remain byte-identical:** parity, scalp_golden (golden baselines not authorized to change).

**Outputs explicitly authorized to change:** Phase 5 test results (new file; zero baseline).

**Golden approval requirement:** Any change to golden baselines requires explicit human approval before committing.

---

## 15. Recommended Implementation Order

### Step 1 — Test file scaffolding + V1-P5-001 + V1-P5-007 (parallel)
- **Tasks:** V1-P5-001, V1-P5-007
- **Files:** `artifacts/tradingview-webhook/test_phase5_execution_safety.py` (new)
- **Objective:** Create test file; prove arm-state boot reset; prove disarm isolation
- **Prerequisite:** Phase 4 green (confirmed)
- **Complexity:** LOW
- **Regression risk:** NONE (additive test only)
- **Runtime change:** None
- **Checkpoint:** All V1-P5-001 + V1-P5-007 tests pass; parity/scalp_golden unchanged

### Step 2 — V1-P5-002 (gateway result contract)
- **Tasks:** V1-P5-002
- **Files:** `test_phase5_execution_safety.py`
- **Objective:** Document gateway_result schema for all paths (paper, manual_only, error cases)
- **Prerequisite:** Step 1 complete
- **Key risk:** Must not assume `ENTRY_PENDING` or `outcome` field (use `status`)
- **Checkpoint:** V1-P5-002 tests pass; gateway contract documented

### Step 3 — V1-P5-004, V1-P5-005, V1-P5-006 (parallel)
- **Tasks:** V1-P5-004, V1-P5-005, V1-P5-006 (all depend on V1-P5-002; independent of each other)
- **Files:** `test_phase5_execution_safety.py`
- **Objective:** Broker rejection isolation; timeout slot-held safety; payload validation local-block
- **Prerequisite:** Step 2 complete
- **Complexity:** LOW-MEDIUM (requires mocking `requests.post`)
- **Regression risk:** None (mock only; no real broker calls)
- **Checkpoint:** V1-P5-004, 005, 006 pass; confirmed no ACTIVE_TRADE on any error path

### Step 4 — V1-P5-003 (BLOCKER — duplicate prevention)
- **Tasks:** V1-P5-003
- **Files:** `test_phase5_execution_safety.py`
- **Objective:** Prove dedup suppression on second identical setup signal
- **Prerequisite:** Step 2 complete; `AUTO_FIRED_KEYS` isolation strategy applied
- **Complexity:** MEDIUM (requires arming AUTO_TRADE, clearing dedup state, verifying suppression)
- **Regression risk:** None (paper mode only; state restored in `finally`)
- **Key setup requirement:** `app.AUTO_FIRED_KEYS.clear()` in test setup; `AUTO_TRADE[inst] = True` before test; both restored in `finally`
- **Checkpoint:** V1-P5-003 passes; dedup suppression log or set membership verified

### Step 5 — V1-P5-008 (paper E2E)
- **Tasks:** V1-P5-008
- **Files:** `test_phase5_execution_safety.py`
- **Objective:** End-to-end paper mode: no broker HTTP call, complete plan dict
- **Prerequisite:** Step 4 complete (V1-P5-003 establishes clean dedup state patterns)
- **Complexity:** LOW (paper mode is well-defined; mock `requests.post` assert-not-called for broker URL)
- **Checkpoint:** V1-P5-008 passes; no broker HTTP confirmed

### Step 6 — Full regression suite
- **Objective:** Confirm all tests, all 4 primaries, py_compile, git diff --check
- **Checkpoint:** Full regression green

### Step 7 — Write `V1_PHASE_5_VALIDATION.md` + commit
- **Commit message:** `V1-P5 Manager and Execution Safety`
- **Contents:** Test file + validation document
- **Restriction:** No `app.py` or production changes in this commit

### Checkpoint summary

| Checkpoint | Gate |
|---|---|
| Research-only | This brief (PHASE_5_EXECUTION_BRIEF.md) |
| Test-first | Step 2 (V1-P5-002 gateway contract) |
| BLOCKER task | Step 4 (V1-P5-003) — must pass before commit |
| Full regression | Step 6 — all prior suites green |
| Commit | Step 7 — single commit after full regression passes |

---

## 16. Risk Register

| Risk ID | Task | Description | Likelihood | Impact | Detection | Mitigation | Stop condition | Rollback |
|---|---|---|---|---|---|---|---|---|
| R-5-01 | V1-P5-003 | `AUTO_FIRED_KEYS` populated from `market_state_cache` restore contaminates dedup test | MEDIUM | HIGH (test passes when it should fail) | Test: first send already suppressed without setting key | `app.AUTO_FIRED_KEYS.clear()` in setup; restore in `finally` | Test infrastructure broken — stop and fix setup | Remove test setup change |
| R-5-02 | V1-P5-003 | `AUTO_TRADE[inst]` left True after test failure causes later tests to fire real gateway calls | MEDIUM | HIGH (unexpected execution) | Other tests unexpectedly trigger paper trades | `try/finally` pattern; restore `AUTO_TRADE[inst] = False` | Any test that sets arm state must use `try/finally` | Restore `AUTO_TRADE` manually |
| R-5-03 | V1-P5-004/005 | Mock `requests.post` affects other concurrent tests (global patching) | LOW | MEDIUM (non-deterministic failures) | Tests pass individually but fail in batch | Use `unittest.mock.patch` as context manager (auto-restores) | Test isolation failure — stop and fix mocking | Remove mock |
| R-5-04 | V1-P5-002/004/005/006 | Tests written against `outcome` field (ARCH wording) instead of `status` field (production) | HIGH | HIGH (all tests fail) | All V1-P5-00x tests fail immediately | Use `status` field per RQ-1 resolution | First run fails; easy to fix | Update assertions |
| R-5-05 | V1-P5-008 | Discord `requests.post` call in paper mode triggers mock assertion failure | MEDIUM | MEDIUM (false negative on broker isolation) | Paper E2E test fails despite correct behavior | Mock only the BROKER URL, not all URLs; or use `side_effect` to allow Discord but block broker | Test logic error — fix mock specificity | Widen mock to all URLs if needed |
| R-5-06 | All | `execute_trade_gateway()` requires a complete `full_analysis()` result for sizing | MEDIUM | MEDIUM (test needs valid market data) | Tests throw KeyError on missing analysis keys | Call `full_analysis(ticker_override="MGC")` and pass snapshot, OR mock `full_analysis` return | Analysis returns error in test context — mock it | Provide mock analysis |
| R-5-07 | V1-P5-003 | Setup_key construction does not match what `_maybe_auto_execute` builds — dedup test silently passes two different keys | MEDIUM | HIGH (BLOCKER task passes incorrectly) | Two paper log entries; no suppression | Use the exact same key construction as production (`f"{inst}:{direction}:{zone_low}"` pattern from code) | Wrong dedup key — rewrite test | Fix key construction |
| R-5-08 | All | Test isolation failure leaves `_TRADERSPOST_LAST` populated, causing later gateway calls to return 429 (cooldown active) | LOW | MEDIUM (unexpected 429 in subsequent tests) | Unexpected `"error"` result in tests that should succeed | Clear `app._TRADERSPOST_LAST.clear()` in test setup if testing live-mode paths | Teardown failure — fix finally blocks | Clear manually |

**Highest-risk task: V1-P5-003** — BLOCKER priority; requires careful state management of `AUTO_FIRED_KEYS`, `AUTO_TRADE`, and dedup key construction.

---

## 17. Phase 5 Execution Contract

### Authorized Task IDs
V1-P5-001, V1-P5-002, V1-P5-003, V1-P5-004, V1-P5-005, V1-P5-006, V1-P5-007, V1-P5-008

### Authorized Production Files
**NONE** — Phase 5 is test-only. `app.py` must NOT be modified.

### Authorized Test Files
- NEW: `artifacts/tradingview-webhook/test_phase5_execution_safety.py`

### Authorized Documentation Files
- NEW: `V1_PHASE_5_VALIDATION.md` (post-implementation)
- NEW: `PHASE_5_EXECUTION_BRIEF.md` (this document — pre-implementation, docs only)

### Authorized Functions (callable read-only in tests)
- `execute_trade_gateway(instrument, contracts, source, direction, expected_stop)` — paper or mock mode only
- `auto_trade_enabled(inst)` — read state
- `active_trade_for(inst)` — read state
- `set_active_trade(inst, trade)` — state injection; MUST be cleaned up in `finally`
- `clear_active_trade(inst, opened_at)` — state cleanup only
- `build_manager_interface(result, instrument)` — read-only builder
- `full_analysis(ticker_override)` — read-only analysis (may be mocked)
- `market_session_status()` — read-only session check

### Authorized Mutable State for Tests (must restore in `finally`)
- `app.AUTO_FIRED_KEYS` — may be cleared and restored (`discard`/`clear` + restore snapshot)
- `app.AUTO_TRADE[inst]` — may be set True for test instrument; must be restored to False
- `app._TRADERSPOST_LAST` — may be cleared to avoid cooldown contamination; restore in `finally`
- `app.ACTIVE_TRADES_BY_INST` — may inject via `set_active_trade()`; must remove via `clear_active_trade()` in `finally`

### Prohibited Runtime Changes
- No changes to `execute_trade_gateway()` behavior, return schema, or field names
- No changes to `_send_broker_order()` return values or exception handling
- No changes to `AUTO_FIRED_KEYS` logic or persistence
- No changes to `AUTO_TRADE` initialization
- No changes to `auto_trade_enabled()`, `set_active_trade()`, `clear_active_trade()`
- No changes to safety controls (kill switch, prop guard, daily loss cap, training mode)
- No changes to broker payload structure or `_broker_required_fields()`
- No changes to `_validate_broker_payload()`
- No gateway calls to live broker URLs — paper or mock mode only
- No database DDL
- No new environment variables

### Canonical Owners (not to be changed)
| Owner | Component | Test can call? |
|---|---|---|
| Expert | `full_analysis()` | Read-only or mocked |
| Manager | `AUTO_TRADE`, `auto_trade_enabled()`, `build_manager_interface()` | Read-only; state injected/restored in `finally` |
| Execution Gateway | `execute_trade_gateway()`, `_send_broker_order()` | Paper/mock mode only |
| Journal | `_build_card_entry()`, Discord sends | Not tested in Phase 5 |
| Coach | Learning functions | Not tested in Phase 5 |

### Interface Restrictions
All 7 canonical interface contracts must remain byte-identical. No `_version` field changes.

### Database Restrictions
No DDL. No `ALTER TABLE`. No new tables. Tests use in-memory state only. If DB is unavailable, tests fail-open (skip DB assertions or expect None).

### Broker Restrictions
No real broker HTTP calls in any Phase 5 test. All live-mode tests must mock `requests.post`. Paper mode is the preferred test mode. `TRADERSPOST_WEBHOOK_URL` must NOT be called.

### Market-Data Restrictions
Tests may use test values for price/stop/target — no live market data required.

### Dashboard Restrictions
No changes to dashboard HTML, CSS, or JavaScript.

### Authentication Restrictions
No changes to Express auth routes or `OPEN_PATHS`. Tests use Flask test client which bypasses Express.

### Required Tests (minimum for commit)
- V1-P5-001: `auto_trade_enabled(inst) == False` at module load for all instruments
- V1-P5-002: `gateway_result.status`, `plan`, `provider`, `mode`, `_version: "v1"` present on success and error paths
- V1-P5-003: Dedup suppression on second identical key (BLOCKER — must pass)
- V1-P5-004: No ACTIVE_TRADE set when broker returns 4xx
- V1-P5-005: No ACTIVE_TRADE set when broker times out; `broker_verify_required == True`
- V1-P5-006: No broker HTTP on invalid payload; `blocked_fields` present
- V1-P5-007: Disarm sets arm False; does not modify ACTIVE_TRADE
- V1-P5-008: Paper mode returns `"simulated"`; no broker HTTP to broker URL

### Required Regressions
All suites listed in Section 14 must pass before commit.

### Required Evidence
- Test output showing all Phase 5 checks passing (0 failed)
- parity / scalp_golden / dual_sim / breakout_mode PASS
- py_compile OK
- git diff --check CLEAN
- V1_PHASE_5_VALIDATION.md written with all 15 sections

### Diff-Control Rules
- `app.py` diff must be EMPTY (no changes)
- `left_brain_market_intelligence.py` diff must be EMPTY
- Only `test_phase5_execution_safety.py`, `V1_PHASE_5_VALIDATION.md`, and `PHASE_5_EXECUTION_BRIEF.md` may appear in the commit diff

### Commit Strategy
One commit after full regression passes:
```
V1-P5 Manager and Execution Safety
```

### Deployment Restrictions
DO NOT DEPLOY. DO NOT PUBLISH. DO NOT RESTART PRODUCTION.

### Stop Conditions
- Any primary regression (parity / scalp_golden / dual_sim / breakout_mode) fails → STOP; do not commit
- Any Phase 4, Phase 3, interface, or Phase 2 test fails unexpectedly → STOP; do not edit existing tests to force green
- `app.py` shows a non-empty diff → STOP; revert any accidental changes
- V1-P5-003 (BLOCKER) fails and cannot be fixed without a production code change → STOP; report as blocking issue

---

## 18. Completion Checklist

- [ ] V1-P5-001: Arm-state boot-reset test passes
- [ ] V1-P5-002: Gateway result contract tests pass; ENTRY_PENDING synchronous model documented
- [ ] V1-P5-003: Duplicate execution prevention test passes (BLOCKER)
- [ ] V1-P5-004: Broker rejection test passes; ACTIVE_TRADE isolation confirmed
- [ ] V1-P5-005: Execution timeout test passes; broker_verify_required=True confirmed
- [ ] V1-P5-006: Payload validation test passes; no broker HTTP on invalid payload
- [ ] V1-P5-007: Safe disarm test passes; disarm-does-not-close invariant proven
- [ ] V1-P5-008: Paper mode E2E passes; no broker HTTP to real URL
- [ ] All new Phase 5 tests pass (0 failed)
- [ ] Phase 4 tests: 57/57 PASS
- [ ] Phase 3 tests: 60/60 PASS
- [ ] Interface tests: 85/85 PASS
- [ ] Phase 2 tests: 45/45 PASS
- [ ] Phase 2 smoke (workspace): PASS
- [ ] Phase 2 smoke (/tmp): PASS
- [ ] parity: PASS (byte-identical)
- [ ] scalp_golden: PASS (byte-identical)
- [ ] dual_sim: PASS + node-check PASS
- [ ] breakout_mode: PASS + node-check PASS
- [ ] py_compile: OK
- [ ] git diff --check: CLEAN
- [ ] `app.py` diff: EMPTY
- [ ] V1_PHASE_5_VALIDATION.md written
- [ ] Commit created: `V1-P5 Manager and Execution Safety`
- [ ] NOT deployed; NOT published

---

## 19. Blocking Issues

**No blocking issues found.** Phase 5 is ready to implement with the following documented resolutions:

1. **RQ-1 (status vs outcome)**: Resolved — tests use `status` field, not `outcome`. ARCH terminology is conceptual only.
2. **RQ-2 (ENTRY_PENDING)**: Resolved — no explicit field; gateway is synchronous; V1-P5-002 documents the actual model.
3. **RQ-3 (smoke script vs pytest)**: Resolved — use Python pytest file per established Phase 2-4 pattern.
4. **RQ-4 (AUTO_FIRED_KEYS isolation)**: Resolved — explicit `clear()` + `finally` restore in test setup.

**Pre-existing technical debt that Phase 5 must not fix:**
- TD-004 (no explicit state machine class) — not Phase 5 scope
- TD-008 (no duplicate test) — V1-P5-003 creates this test; must not change the production code that enables the test

---

## 20. Recommendation: Ready or Not Ready to Implement

**READY TO IMPLEMENT.**

All 8 Phase 5 tasks are test-writing tasks with no production code changes required. Every piece of behavior they need to verify already exists in the production codebase. The research questions are fully resolved. The implementation order is clear. The state isolation patterns (try/finally, mock patching via `unittest.mock.patch.object`) are established from Phases 3 and 4.

**Priority implementation note:** V1-P5-003 is the BLOCKER task with the highest implementation score in Phase 5. It must be implemented and pass before Phase 5 can commit. Steps 1 and 2 should be completed first to establish the test file structure and gateway contract. V1-P5-004, V1-P5-005, and V1-P5-006 can be written in parallel after Step 2. V1-P5-008 should come after V1-P5-003.

**Expected new test count:** ~40-60 runtime checks across 8 task groups in one test file.

**Expected `app.py` diff:** EMPTY.

**Phase 6 prerequisite:** V1-P5 must be complete before Phase 6 (Journal and Coach Separation) begins, per Stream E → Stream F dependency.
