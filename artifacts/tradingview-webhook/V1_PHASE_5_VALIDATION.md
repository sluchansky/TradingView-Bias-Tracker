# V1 Phase 5 — Manager and Execution Safety: Validation Report

**Branch:** `polish-v1`  
**Starting HEAD (b00988c audit entry point):** `b00988c`  
**Ending HEAD:** see "Commit" section below  
**Date:** 2026-07-30  
**Status:** ✅ COMPLETE — audit passed, no production correction required

---

## Phase 5 Implementation Summary

Phase 5 adds the ARCH §9 `outcome` field (additive, backward-compatible) to every
`execute_trade_gateway` result dict via a wrapper-plus-centralized-derivation pattern.

---

## Stage 0 — Freeze and Diff

**Branch:** `polish-v1`  
**git status at audit entry:** clean working tree (only one untracked asset file)  
**git diff --check:** no whitespace errors

**Commits from c20c7ca through b00988c:**

```
b00988c  V1-P5 Manager and Execution Safety
f449371  Add Phase 5 pre-implementation execution brief document
c20c7ca  (accepted brief)
```

**Files changed in b00988c:**

| File | Classification | Lines changed |
|---|---|---|
| `artifacts/tradingview-webhook/V1_PHASE_5_VALIDATION.md` | new — validation documentation | +133 |
| `artifacts/tradingview-webhook/app.py` | production | +67 / -9 |
| `artifacts/tradingview-webhook/test_phase5_execution_safety.py` | new — Phase 5 test | +1111 |
| `artifacts/tradingview-webhook/test_v1_interface_versions.py` | existing test modification | +17 / -7 |

No other files changed. No unrelated changes.

---

## Stage 1 — Exact File Inventory

### Existing test modified: `test_v1_interface_versions.py`

**Exact function changed:** `_gateway_fn_src()` (lines 172–181, pre-change)

**Before:**
```python
def _gateway_fn_src():
    src = _app_src()
    start = src.find("def execute_trade_gateway(")
    assert start >= 0, "execute_trade_gateway not found in app.py"
    next_def = src.find("\ndef ", start + 1)
    end = next_def if (0 < next_def - start < 200_000) else start + 55_000
    return src[start:end]
```

**After:**
```python
def _gateway_fn_src():
    """Return the combined source of the gateway implementation.

    execute_trade_gateway() was refactored into:
      _execute_trade_gateway_inner() — all actual logic, status strings, _version fields
      _gw_outcome()                  — centralized outcome mapping helper
      execute_trade_gateway()        — thin public wrapper (additive 'outcome' field only)

    All three are part of the gateway surface, so we return the source of the entire
    block from _execute_trade_gateway_inner through to the start of _advisor_blocks_auto_trade.
    """
    src = _app_src()
    start = src.find("def _execute_trade_gateway_inner(")
    assert start >= 0, "_execute_trade_gateway_inner not found in app.py"
    end_marker = src.find("def _advisor_blocks_auto_trade(", start)
    end = end_marker if (0 < end_marker - start < 200_000) else start + 55_000
    return src[start:end]
```

**Why it was modified:** The old helper found `def execute_trade_gateway(` as its start
point. After the wrapper refactor, `execute_trade_gateway` is a 15-line thin wrapper with
no status literals and no `_version` fields. The old helper reading only the wrapper would
return a range too small to satisfy any of the 4 consuming assertions.

**Assertions that consume the helper:**
1. `test_gateway_manual_required_version_in_source` — checks `'"status": "manual_required"' in gw` + `'"_version": "v1"' in gw[idx:idx+400]`
2. `test_gateway_simulated_version_in_source` — checks `'"status": "simulated"' in gw` + `'"_version": "v1"' in gw[idx:idx+400]`
3. `test_gateway_sent_version_in_source` — checks `'"status": "sent"' in gw` + `'"_version": "v1"' in gw[idx:idx+400]`
4. `test_gateway_version_count` — checks `gw.count('"_version": "v1"') == 3`
5. `test_cross_interface_version_matrix` (via helper at line 1087) — checks `gw.count('"_version": "v1"') >= 3`

**Whether assertion strength changed:** NO — see Stage 2 for the proof.

**Whether any test case was removed, relaxed, skipped, or reclassified:** NO.
No test was removed, renamed, or changed in any way other than the helper update.

---

## Stage 2 — Interface-Test Integrity Audit

### Q1: Were the failures caused only by source-inspection boundaries?

**Yes.** The old helper found `def execute_trade_gateway(` and read to the next `\ndef `.
After the wrapper refactor, `execute_trade_gateway` is a 15-line function containing no
status literals — so the old helper returned a range that satisfied none of the assertions.
Runtime behavior was never affected.

### Q2: Did runtime interface behavior remain identical?

**Yes.** `execute_trade_gateway` is still callable, returns `(dict, int)`, accepts the
same arguments, and all callers produce the same results. Confirmed by: P4 57/57,
P3 60/60, P2 45/45, interface 85/85, all 4 smoke checks PASS throughout.

### Q3: Does the modified helper still inspect all code it inspected before?

**Yes, and more.** The old range was: `def execute_trade_gateway(` → `def _advisor_blocks_auto_trade(`.  
The new range is: `def _execute_trade_gateway_inner(` → `def _advisor_blocks_auto_trade(`.  
New range is a **strict superset** of the old (same endpoint, earlier start).

### Q4: Does it inspect additional code that could create false positives?

**No.** The new range additionally includes `_gw_outcome` and the public wrapper.
Runtime-confirmed: neither contains any of the assertion-target strings:

| Assertion string | In `_gw_outcome` | In wrapper |
|---|---|---|
| `'"status": "sent"'` | False | False |
| `'"status": "simulated"'` | False | False |
| `'"status": "manual_required"'` | False | False |
| `'"_version": "v1"'` | False | False |

Every assertion is still backed solely by evidence in `_execute_trade_gateway_inner`.

### Q5: Could the updated helper pass if the public wrapper omitted required fields?

**No — and this is by design.** The source-inspection assertions check that
`_execute_trade_gateway_inner` contains the correct status literals and `_version` fields.
Whether the wrapper adds `outcome` is proven by the *runtime* assertions in
`test_phase5_execution_safety.py` (P5-002, P5-008), not by source inspection.
The two test files are complementary: interface tests guard the gateway structure;
P5 tests guard the runtime outcome contract.

### Q6: Did any source-based assertion become weaker?

**No.** All assertion strings are exclusive to `_execute_trade_gateway_inner`.
The `== 3` count for `_version` is still exactly 3 in the inner function (confirmed by
test S4-16-a). The `+/-400` proximity windows are unaffected because the inner function
is structurally unchanged.

### Q7: Can the original test remain unchanged with a smaller production change?

**Yes (Approach C, see Stage 7)** — but Approach C requires ~53 individual inline edits
inside the function body, which is less safe and more invasive than the wrapper (Stage 7
provides the full comparison). Approach A (current) is the correct choice.

---

## Stage 3 — Public Function Compatibility Audit

### Signature comparison

| Attribute | Pre-Phase-5 | Post-Phase-5 (wrapper) |
|---|---|---|
| `def` name | `execute_trade_gateway` | `execute_trade_gateway` — identical |
| Param 1 | `instrument` (positional, required) | identical |
| Param 2 | `contracts` (positional, required) | identical |
| Param 3 | `source="manual"` | identical |
| Param 4 | `direction=None` | identical |
| Param 5 | `expected_stop=None` | identical |
| Decorators | none | none |
| Annotations | none | none |
| Return shape | `(dict, int)` | `(dict, int)` — identical |

### Caller survey (all references to `execute_trade_gateway` in the codebase)

| Location | Line | Classification | Compatible? |
|---|---|---|---|
| `app.py` L44940 | `execute_trade_gateway(instrument, contracts, source="manual")` | runtime call | ✓ |
| `app.py` L44985 | `execute_trade_gateway(instrument, contracts, ...)` | runtime call | ✓ |
| `app.py` L45080 | `execute_trade_gateway(instrument, contracts, ...)` | runtime call | ✓ |
| `app.py` L1868 | comment block | documentation | ✓ |
| `app.py` L26191 | comment block | documentation | ✓ |
| `app.py` L38416 | comment block | documentation | ✓ |
| `app.py` L39899 | comment block | documentation | ✓ |
| `app.py` L44913 | comment block | documentation | ✓ |
| `test_dpv2_phase3.py` L253 | `setattr(app, fn_name, _make_stub(fn_name))` | monkeypatch target | ✓ |
| `test_cockpit_migration.py` L216 | `assertIn("execute_trade_gateway", self.app)` | source inspection | ✓ |
| `validate_dpv2_production.py` L170 | `"execute_trade_gateway"` in `GATEWAY_FUNCTIONS` | source inspection | ✓ |
| `validate_dpv2_production.py` L558 | string check in call chain | source inspection | ✓ |
| `test_persistence.py` L405 | comment in test docstring | documentation | ✓ |
| `test_tfa.py` L1663 | comment in test docstring | documentation | ✓ |

**`inspect.getsource` usage:** No code in the repository calls
`inspect.getsource(app.execute_trade_gateway)`. Only `full_analysis`,
`DatabentoBrain._on_bar_close`, and `compute_scalp_quality` are inspected via
`getsource`. This is a non-issue.

### Monkeypatch compatibility

`setattr(app, "execute_trade_gateway", stub)` replaces `app.__dict__["execute_trade_gateway"]`,
which is the same dict that internal callers resolve via module globals. All internal callers
reference `execute_trade_gateway` (not `_execute_trade_gateway_inner`), so monkeypatching the
public name correctly intercepts all gateway calls — identical behavior to pre-Phase-5.

### Exception behavior

The wrapper contains no `try/except`. Any exception raised inside
`_execute_trade_gateway_inner` propagates through the wrapper unchanged. Proven by test S4-12.

---

## Stage 4 — Caller and Patch-Boundary Tests

Added as class `TestP5_Stage4_CompatibilityProof` in `test_phase5_execution_safety.py`:

| Test | Proves |
|---|---|
| S4-01 | Public signature: 5 params, correct names, correct defaults |
| S4-02 | Inner function has identical signature to public wrapper |
| S4-03 | `execute_trade_gateway` is a public callable module attribute |
| S4-04 | `_execute_trade_gateway_inner` is accessible for test isolation |
| S4-05 | `setattr(app, "execute_trade_gateway", spy)` intercepts direct calls |
| S4-06 | `mock.patch.object(app, "execute_trade_gateway", ...)` intercepts correctly |
| S4-07 | Wrapper calls inner exactly once per call |
| S4-08 | Wrapper preserves exact HTTP code (tested for 200, 400, 409, 429, 502) |
| S4-09 | Wrapper preserves all pre-existing result fields unchanged |
| S4-10 | Wrapper adds exactly one key: `outcome` |
| S4-11 | Wrapper does not overwrite `outcome` if inner already set it |
| S4-12 | Exceptions from inner propagate unchanged through wrapper |
| S4-13 | Repeated calls do not share result dicts |
| S4-14 | Return is always a `(dict, int)` tuple |
| S4-15 | Source-inspection assertions: status strings in inner, NOT in `_gw_outcome` or wrapper |
| S4-16 | Exactly 3 `_version: v1` insertions remain in inner (unchanged) |

---

## Stage 5 — Outcome Return-Path Matrix

`_execute_trade_gateway_inner` has **~55 distinct return paths**, categorized below.
All receive `outcome` either directly (set in the returning function) or via the wrapper's
`_gw_outcome()` call.

### Paths where `outcome` is set **directly** (before wrapper)

| Function | Condition | `status` | `outcome` | HTTP | Broker | Slot |
|---|---|---|---|---|---|---|
| `_training_suppressed_result()` | Training mode, stages 1–3 | `manual_required` | `manual_required` | 200 | No | N/A |
| `_send_broker_order()` | `blocked_fields` payload failure | `error` | `invalid_payload` | 400 | No | Released |
| `_send_broker_order()` | `requests.RequestException` | `error` | `timeout` | 502 | Yes (ambiguous) | Held |
| `_send_broker_order()` | 4xx from broker | `error` | `broker_rejected` | 502 | Yes | Released |
| `_send_broker_order()` | 5xx from broker | `error` | `broker_rejected` | 502 | Yes (ambiguous) | Held |

### Paths where wrapper's `_gw_outcome()` adds `outcome`

| Condition | `status` | `outcome` | HTTP | Broker | Slot |
|---|---|---|---|---|---|
| Unknown/unsupported instrument | `error` | `invalid_payload` | 400 | No | N/A |
| `contracts` not a whole number | `error` | `invalid_payload` | 400 | No | N/A |
| Trade plan parse error | `error` | `invalid_payload` | 400 | No | N/A |
| `manual_only` mode | `manual_required` | `manual_required` | 200 | No | N/A |
| `paper` mode | `simulated` | `paper` | 200 | No | N/A |
| Broker 2xx (single-order success) | `sent` | `sent` | 200 | Yes | Held |
| `_execute_live_two_leg_entry()` success | `sent` | `sent` | 200 | Yes | Held |
| Duplicate cooldown suppressed | `error` | `rejected` | 429 | No | Held |
| Prop guard block | `error` | `rejected` | 409 | No | N/A |
| All other safety/gate/mode/config errors (~49) | `error` | `rejected` | 400/409 | No | N/A |

### `_gw_outcome()` mapping

```
status == "sent"            → "sent"
status == "simulated"       → "paper"
status == "manual_required" → "manual_required"
status == "error" + blocked_fields present     → "invalid_payload"
status == "error" + "Unknown instrument" in reason  → "invalid_payload"
status == "error" + "contracts must be a whole number" in reason → "invalid_payload"
status == "error" + "Could not read trade plan" in reason  → "invalid_payload"
status == "error" + anything else              → "rejected"
```

**Confirmed no branch returns:** missing outcome, `outcome=None`, unknown outcome,
or outcome inconsistent with status/reason. Proven by test classes P5-002, P5-008
(runtime), and S4-15 (source inspection).

---

## Stage 6 — 5xx Cooldown-Slot Proof

Added as class `TestP5_Stage6_5xxSlotRetention` in `test_phase5_execution_safety.py`:

### 4xx vs 5xx vs Timeout slot behavior

| Condition | Slot action | `broker_verify_required` | Rationale |
|---|---|---|---|
| 4xx (definite rejection) | **Released** (`_release_slot()` called) | False | Order never placed; retry safe |
| 5xx (ambiguous) | **Held** (no `_release_slot()`) | True | Order may be live; verify before retry |
| `RequestException` (timeout/network) | **Held** (no `_release_slot()`) | True | Bytes may have reached broker; verify before retry |

### Tests proving the 5xx invariant

| Test | Proves |
|---|---|
| S6-01 | 5xx outcome = `broker_rejected` |
| S6-02 | 5xx sets `broker_verify_required = True` |
| S6-03 | `release_slot` NOT called on 5xx (`_send_broker_order` direct test) |
| S6-04 | 5xx returns HTTP 502 |
| S6-05 | 4xx contrast: `release_slot` IS called, `broker_verify_required` is False |
| S6-06 | Timeout contrast: `release_slot` NOT called, `outcome = "timeout"` |
| S6-07 | Full-gateway 5xx: `_TRADERSPOST_LAST[inst]` is populated (slot held) |
| **S6-08** | **Full-gateway 5xx: second immediate call makes ZERO additional broker HTTP calls** |
| S6-09 | Full-gateway 4xx: `_TRADERSPOST_LAST[inst]` is None (slot released) |
| S6-10 | Summary: 5xx slot held, 4xx slot released, verified via `_TRADERSPOST_LAST` inspection |

**S6-08 is the critical end-to-end proof:** after a 5xx, the dedup fingerprint remains in
`_TRADERSPOST_LAST`. The second call (same instrument, same direction, same price) computes
the identical fingerprint, hits the cooldown check at the top of the live-provider block,
and returns `{"status": "error", "reason": "Duplicate order suppressed..."}` with HTTP 429
— never reaching `_send_broker_order`. Zero additional broker HTTP calls confirmed.

---

## Stage 7 — Final Design Decision

### Approaches compared

**Approach A — Current (wrapper + inner function):**
- Rename `execute_trade_gateway` → `_execute_trade_gateway_inner` (1 def-line edit)
- Add `_gw_outcome()` centralized helper
- Add thin `execute_trade_gateway()` public wrapper
- Requires 1 test helper update (non-weakening superset, proven in Stage 2)
- Exhaustive outcome coverage guaranteed: wrapper intercepts every path, impossible to miss a return
- Production diff: ~67 lines in `app.py`

**Approach B — Centralized return path (not applicable):**
`_execute_trade_gateway_inner` has ~55 distinct return paths with no single exit point.
A centralized post-return hook would require the same wrapper structure. Not meaningfully
different from Approach A.

**Approach C — No rename; inline `outcome` at each return site:**
- Keep `def execute_trade_gateway(...)` unchanged (no rename)
- Add `_gw_outcome()` helper
- Add `"outcome": _gw_outcome(...)` inline at every return dict (~53 error returns + 3 success returns = ~56 total edits)
- `_gateway_fn_src()` unchanged (no test modification needed)
- Production diff: ~60 inline dict edits across the function body
- Higher risk: each return site must be individually identified and edited; one missed site = missing outcome on that path
- `inspect.getsource(app.execute_trade_gateway)` returns full body

### Verdict: Retain Approach A

**Approach A is the correct choice for the following reasons:**

1. **Exhaustive coverage by construction:** The wrapper intercepts every return of
   `_execute_trade_gateway_inner`. It is impossible to miss a path — new return sites added
   later are automatically covered. Approach C requires every future contributor to remember
   to add `"outcome"` to each new return dict.

2. **Single mapping location:** `_gw_outcome()` is the single authoritative definition of
   the outcome vocabulary. Approach C disperses equivalent logic across 56 inline expressions.

3. **Test modification is non-weakening:** Stage 2 proves rigorously that the helper update
   returns a superset of the original range, all assertions are backed by the same evidence
   (the inner function), and no assertion was relaxed. The modification is justified and safe.

4. **No `inspect.getsource` usage:** No code in the repository calls
   `inspect.getsource(app.execute_trade_gateway)`, removing the one theoretical advantage
   of Approach C.

5. **Production diff size is comparable:** Approach A: ~67 lines in `app.py`. Approach C:
   ~56 + helper = ~70 lines in `app.py`. The wrapper approach is not larger.

---

## Stage 8 — Authorized Correction

**No production correction required.** The audit found no scope violation or compatibility
issue. The test file addition (Stage 4 + Stage 6 tests) is purely additive and strengthens
the evidence base.

---

## Full Regression Results

### Post-audit (final state)

| Suite | Command | Count | Result |
|---|---|---|---|
| Phase 5 tests (all classes incl. Stage 4 + 6) | `pytest test_phase5_execution_safety.py` | 109 | ✅ PASS |
| Phase 4 tests | `pytest test_phase4_operator_explanation.py` | 57 | ✅ PASS |
| Interface tests | `pytest test_v1_interface_versions.py` | 85 | ✅ PASS |
| Phase 3 tests | `pytest test_phase3_thesis_verdict_pipeline.py` | 60 | ✅ PASS |
| Phase 2 tests | `pytest test_phase2_market_data_reliability.py` | 45 | ✅ PASS |
| Parity smoke | `bash .local/state/check_parity.sh` | — | ✅ PARITY OK |
| Scalp golden | `bash .local/state/check_scalp_golden.sh` | — | ✅ BYTE-IDENTICAL |
| Dual-sim smoke | `bash .local/state/check_dual_sim.sh` | — | ✅ SMOKE OK |
| Breakout mode smoke | `bash .local/state/check_breakout_mode.sh` | — | ✅ SMOKE OK |
| Python syntax | `py_compile app.py` | — | ✅ OK |
| Whitespace | `git diff --check HEAD` | — | ✅ CLEAN |

---

## Final Report

1. **Starting HEAD:** `b00988c` (V1-P5 Manager and Execution Safety)
2. **Ending HEAD:** see commit below
3. **Files changed in b00988c:** 4 files (production, new P5 test, existing test modification, validation doc)
4. **Existing test file modified:** `test_v1_interface_versions.py`
5. **Exact reason:** `_gateway_fn_src()` helper found `def execute_trade_gateway(` (the new thin wrapper) instead of the actual gateway body; the wrapper contains no status literals or `_version` fields
6. **Assertion strength changed:** No — new range is a superset; all assertions backed by the same inner-function evidence; S4-15/S4-16 prove this at runtime
7. **Original test can be restored with smaller change:** Yes (Approach C), but Approach C is more invasive, higher-risk, and less maintainable; Approach A is the better design
8. **Original signature:** `execute_trade_gateway(instrument, contracts, source="manual", direction=None, expected_stop=None)` — wrapper signature is **identical**
9. **Caller compatibility:** All 3 runtime callers and all source/monkeypatch references work correctly
10. **Monkeypatch compatibility:** `setattr(app, "execute_trade_gateway", stub)` and `mock.patch.object` both intercept correctly; internal callers use module globals (same dict)
11. **Return-shape compatibility:** `(dict, int)` on every path — unchanged
12. **Exception compatibility:** Wrapper has no try/except; all inner exceptions propagate unchanged (S4-12)
13. **Outcome coverage:** All ~55 return paths receive a valid `outcome` from one of: `_send_broker_order` (direct), `_training_suppressed_result` (direct), or `_gw_outcome()` via wrapper
14. **Missing-outcome paths:** None
15. **Unknown-outcome paths:** None
16. **4xx slot behavior:** `_release_slot()` called → `_TRADERSPOST_LAST` cleared → retry permitted (S6-05, S6-09)
17. **5xx slot behavior:** `_release_slot()` NOT called → `_TRADERSPOST_LAST` held → immediate retry suppressed (S6-03, S6-07, S6-08)
18. **Timeout slot behavior:** `_release_slot()` NOT called → `_TRADERSPOST_LAST` held → same as 5xx (S6-06)
19. **Immediate duplicate call counts after 5xx:** 0 additional broker HTTP calls (S6-08)
20. **Chosen final implementation design:** Approach A (wrapper) retained; no correction
21. **Corrective files changed:** None to production; `test_phase5_execution_safety.py` extended with Stage 4 (16 tests) and Stage 6 (10 tests) proofs
22. **Phase 5 tests:** 109 passed (original 83 + 16 Stage 4 + 10 Stage 6)
23. **Interface tests:** 85 passed (unchanged from b00988c)
24. **All regressions:** Green (see regression table above)
25. **Validation document:** Updated with complete audit (this file)
26. **Commit:** `V1-P5 final compatibility audit — no production correction required`
27. **b00988c acceptable as-is:** YES — no compatibility issue, no weakened assertion, no missing outcome path
28. **Phase 5 officially complete:** YES
29. **Deployment confirmation:** Not deployed. No deployment requested or performed.
30. **Phase 6 may begin:** Per audit doc instructions: NO. Await explicit authorization.
