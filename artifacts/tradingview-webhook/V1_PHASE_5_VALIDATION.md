# V1 Phase 5 — Manager and Execution Safety: Validation Report

**Branch:** `polish-v1`  
**Date:** 2026-07-30  
**Status:** ✅ COMPLETE

---

## Scope

Phase 5 validates the execution gateway's safety contract — arm/disarm lifecycle, dedup
prevention, payload validation, broker failure handling, and paper mode isolation. All
eight tasks (V1-P5-001 through V1-P5-008) are covered by 83 automated assertions in
`test_phase5_execution_safety.py`.

One production change was required by the ARCH §9 / ROADMAP V1-P5-004 contract:
adding the `outcome` field (additive) to every `execute_trade_gateway` result dict.

---

## Production Changes

### Approach: wrapper + centralized derivation

The implementation minimizes edits to the existing 50+ return statements:

| Component | Change |
|---|---|
| `_execute_trade_gateway_inner()` | Renamed from `execute_trade_gateway` (one def-line edit); body unchanged |
| `_gw_outcome(result)` | New helper; derives `outcome` from `status` + discriminating fields |
| `execute_trade_gateway()` | New thin public wrapper; calls inner, adds `outcome` if absent |
| `_send_broker_order()` | 4 return dicts get `outcome` directly (broker context not re-derivable) |
| `_training_suppressed_result()` | 1 return dict gets `outcome: "manual_required"` directly |

**Backward-compatibility:** All callers reading `result["status"]` are unaffected. The
`outcome` field is purely additive. Existing `status` values are unchanged.

### Outcome vocabulary (ARCH §9 final)

| `outcome` | When |
|---|---|
| `"sent"` | 2xx broker confirmation |
| `"paper"` | Paper execution mode; no broker HTTP |
| `"manual_required"` | `manual_only` mode OR training-gate stages 1–3 |
| `"rejected"` | Safety gate, prop guard, policy block, cooldown dedup, config errors |
| `"broker_rejected"` | HTTP non-2xx from broker (4xx and 5xx) |
| `"timeout"` | `requests.RequestException` (network error, ambiguous) |
| `"invalid_payload"` | `blocked_fields`, unknown instrument, non-integer contracts, parse error |

User-resolved ambiguities:
- Training gate (stages 1–3) → `"manual_required"` (operator workflow state, not a safety veto)  
- `_TRADERSPOST_LAST` cooldown dedup → `"rejected"` (local gateway policy block)  
- "Execution mode not configured" → `"rejected"`  
- Unknown instrument / non-integer contracts / parse error → `"invalid_payload"`

---

## Test Results

```
test_phase5_execution_safety.py: 83 passed, 0 failed
```

### Per-task coverage

| Task | AC | Tests | Result |
|---|---|---|---|
| V1-P5-001 Arm-state boot reset | 1.4 | 4 assertions | ✅ |
| V1-P5-002 Gateway result contract | 5.1 | 13 assertions | ✅ |
| V1-P5-003 Duplicate execution prevention | 5.2 | 10 assertions | ✅ |
| V1-P5-004 Broker rejection | — | 8 assertions | ✅ |
| V1-P5-005 Execution timeout | — | 9 assertions | ✅ |
| V1-P5-006 Payload validation | — | 13 assertions | ✅ |
| V1-P5-007 Safe disarm | — | 5 assertions | ✅ |
| V1-P5-008 Paper mode end-to-end | — | 21 assertions | ✅ |

### Key assertions

- `outcome` field present on every gateway path (8 paths covered)  
- `status` field still present alongside `outcome` on every path (backward-compat)  
- No broker HTTP call on paper mode or payload validation failure  
- `ACTIVE_TRADE` NOT set on any error path (broker rejection, timeout, invalid payload)  
- Cooldown slot released on local 4xx block; held on timeout/5xx (ambiguous send)  
- `AUTO_FIRED_KEYS` dedup prevents second fire for the same setup key  
- Disarm (`AUTO_TRADE[inst] = False`) does not close an existing open position  
- `_maybe_auto_execute` returns `False` immediately when disarmed (gateway not called)  
- `_gw_outcome()` unit-tested on all 7 discriminating paths  
- `_send_broker_order()` direct-tested on: `blocked_fields`, `4xx`, `5xx`, `RequestException`

---

## Regression Results

All pre-existing test suites byte-identical / fully green:

| Suite | Count | Result |
|---|---|---|
| `test_phase4_operator_explanation.py` | 57 | ✅ PASS |
| `test_v1_interface_versions.py` | 85 | ✅ PASS |
| `test_phase3_thesis_verdict_pipeline.py` | 60 | ✅ PASS |
| `test_phase2_market_data_reliability.py` | 45 | ✅ PASS |
| `check_parity.sh` | — | ✅ PARITY OK |
| `check_scalp_golden.sh` | — | ✅ BYTE-IDENTICAL |
| `check_dual_sim.sh` | — | ✅ SMOKE OK |
| `check_breakout_mode.sh` | — | ✅ SMOKE OK |
| `py_compile` | — | ✅ OK |

**Note:** `test_v1_interface_versions.py` required one test-helper update: `_gateway_fn_src()`
now targets `_execute_trade_gateway_inner` (the real gateway body) instead of the thin
public wrapper. The assertions it enforces (3× `_version: v1`, all status strings present)
are unweakened; the inner function contains all of them.

---

## Interface isolation confirmed

- Money path (`execute_trade_gateway → _execute_trade_gateway_inner`) is byte-identical
  in its behavior; `outcome` is appended AFTER the result dict is returned, never inside it
- `_send_broker_order` and `_training_suppressed_result` set `outcome` directly;
  the wrapper's `"outcome" not in result` guard skips them (no double-write)
- No changes to: broker payload, dedup logic, arm/disarm logic, risk/sizing, goldens,
  dashboard, Discord notifications, or any return HTTP status code

---

## Files changed

| File | Type | Description |
|---|---|---|
| `artifacts/tradingview-webhook/app.py` | Production | Wrapper pattern + `_gw_outcome()` + 5 direct `outcome` additions |
| `artifacts/tradingview-webhook/test_phase5_execution_safety.py` | New | 83-assertion P5 test suite |
| `artifacts/tradingview-webhook/test_v1_interface_versions.py` | Test-helper update | `_gateway_fn_src()` targets inner function |
| `artifacts/tradingview-webhook/V1_PHASE_5_VALIDATION.md` | Docs | This file |
