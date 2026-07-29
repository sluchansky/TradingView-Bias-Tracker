# Phase 4 Execution Brief — Operator Explanation and Decision Timeline

**Status:** Pre-implementation documentation only  
**DO NOT MODIFY CODE. DO NOT DEPLOY. DO NOT PUBLISH.**

---

## 1. Executive Summary

Phase 4 verifies that the seven decision-state explanations surface correctly in `/status`, confirms the Partner compute path degrades gracefully on failure, audits Operator Mode for DIAGNOSTIC-tier content leakage, and verifies the decision timeline is accessible from Engineering View under owner auth.

Phase 4 is a **verification-and-test phase** (Stream D). No gate logic, verdict production, Partner compute path, or execution behavior may change. The only authorized code change is a small CSS/HTML correction to Operator Mode if V1-P4-009 discovers DIAGNOSTIC content displayed to operators — and only if such content is present.

Ten tasks are in scope: V1-P4-001 through V1-P4-010.

**Pre-implementation research required** before coding begins: four tasks (V1-P4-003, V1-P4-004, V1-P4-005, V1-P4-010) have implementation gaps that must be resolved during this brief phase. They are documented in Section 10 (Risk Register).

---

## 2. Baseline State

| Field | Value |
|---|---|
| **Branch** | `polish-v1` |
| **Current HEAD** | `7faa0e7` — Add intervening commit provenance audit document |
| **Accepted Phase 3 chain** | `64f6d35` → `29e1d4d` → `aa7ee5f` → `ee808db` |
| **`git status`** | Only attached instruction file untracked — working tree CLEAN |
| **`git diff --check`** | CLEAN |

### Commits Since ee808db

| SHA | Message | Classification |
|---|---|---|
| `7faa0e7` | Add intervening commit provenance audit document | **Attached asset / documentation** — `attached_assets/*.txt` only; no source changes |

**Conclusion:** Working tree is clean. No unexplained code changes since `ee808db`. Implementation may begin from `7faa0e7`.

---

## 3. Phase 4 Overview

| Field | Value |
|---|---|
| **Title** | Operator Explanation and Decision Timeline |
| **Stream** | D |
| **Task count** | 10 (V1-P4-001 through V1-P4-010) |
| **Phase type** | Verification-and-test (predominantly test-only; one conditional code fix) |
| **Predecessor** | Phase 3 — Thesis and Verdict Pipeline (COMPLETE at `ee808db`) |
| **Successor** | Phase 5 — Manager and Execution Safety |
| **Must not change** | Main Brain synthesis, verdict production, Partner compute path |

### Document Precedence Note

`PRODUCT_SPEC_V1.md` uses "Phase 4: Learning" at its line 1941, which appears to be a product-level feature taxonomy distinct from the implementation phase numbering in `IMPLEMENTATION_ROADMAP_V1.md`. Since `IMPLEMENTATION_ROADMAP_V1.md` has higher precedence (rank 2 vs. rank 3) and explicitly names Phase 4 as "Operator Explanation and Decision Timeline" at line 1050, no conflict exists. The product spec's phase numbering is a parallel classification scheme — not a behavioral specification for this implementation phase. **No stop is required.**

### Exit Criteria

- All 7 decision-state explanations verified at the API level (`/status` JSON)
- Partner-failure fallback test passes (no 500 on `compute_main_brain()` exception)
- Operator Mode audit complete (no DIAGNOSTIC content in operator panels, or violation corrected)
- All 4 primary regressions pass
- All existing 190 tests pass

---

## 4. Complete Task Inventory

### V1-P4-001: WAIT State Explanation

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-001 |
| **Title** | Verify WAIT state explanation: `strict_reason` present and non-empty in `/status` |
| **Purpose** | Confirm the platform always names the blocking condition when verdict is WAIT |
| **Priority** | MEDIUM |
| **Implementation owner** | `full_analysis()` → `_build_status_payload()` → `strict_reason` key (line 44501) |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `full_analysis()`, `_build_status_payload()`, `/status` Flask endpoint |
| **Interfaces touched** | Expert (read-only verification only) |
| **Runtime behavior changes** | NONE — verification test only |
| **Prohibited changes** | Must not change gate logic, verdict strings, or strict_reason assignment |
| **Required validation** | Test that `/status` `strict_reason` is non-empty string when verdict is WAIT; repeat for market-closed state |
| **Completion criteria** | Runtime assertion: `strict_reason.strip() != ""` on `/status` JSON when `verdict` is WAIT |
| **Implementation risk** | LOW — `strict_reason` confirmed present at line 44501; market-closed state always WAIT in test env |
| **Pre-existing code confirmed at 29e1d4d** | YES |
| **Note** | V1-P3-005 already wrote 4 tests for this contract. Phase 4 may extend coverage or reference Phase 3 tests as implementation-inherited evidence. |

---

### V1-P4-002: READY State Explanation

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-002 |
| **Title** | Verify READY state explanation: `main_brain_voice`, `grade`, `edge_score`, `trade_plan` all present |
| **Purpose** | Confirm all four required fields of the READY explanation are always populated in `/status` |
| **Priority** | MEDIUM |
| **Implementation owner** | `compute_main_brain_voice()` (line 20392), `_build_status_payload()` keys at lines 44530/44535/44524/44557 |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `full_analysis()`, `_build_status_payload()` |
| **Interfaces touched** | Expert (read-only), Partner (read-only via `main_brain_voice`) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change Partner compute path |
| **Required validation** | Assert presence and correct types of all four fields in `/status` JSON; verify `main_brain_voice` is non-empty string |
| **Completion criteria** | 4 fields verified present + typed in `/status` |
| **Implementation risk** | MEDIUM — READY verdict cannot be forced in test environment (market always closed). Tests must either: (a) verify field presence across all verdicts (fields are always populated, not READY-only), or (b) use `full_analysis()` directly with state injection |
| **Pre-existing code confirmed at 29e1d4d** | YES |

---

### V1-P4-003: EARLY State Explanation

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-003 |
| **Title** | Verify EARLY state explanation: `alert_level="EARLY"`, `potential_plan` present |
| **Purpose** | Confirm EARLY-tier setup shows the pre-READY advisory and a forming trade plan |
| **Priority** | MEDIUM |
| **Implementation owner** | `alert_level` in `full_analysis()` result; `potential_plan` nested inside `result["directions"][*]["potential_plan"]` (line 23848); `/status` `alert_level` key at line 44583 |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `full_analysis()`, `_build_status_payload()`, `evaluate_strict_setup()` |
| **Interfaces touched** | Expert (read-only) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change EARLY tier logic or alert_level assignment |
| **Required validation** | (a) `alert_level` key present in `/status`; (b) when `alert_level="EARLY"`, `potential_plan` is non-None inside `directions[*]`; (c) structural assertion that `potential_plan` is a dict |
| **Completion criteria** | `alert_level` verified present in `/status`; `potential_plan` nested structure verified via `directions` block |
| **Implementation risk** | HIGH — EARLY verdict cannot be forced in test environment. `potential_plan` is NOT a top-level `/status` key — it is nested inside `result["directions"][direction]["potential_plan"]`. Approach: verify structural schema of the `directions` block, then assert `potential_plan` contract on synthetic `evaluate_strict_setup()` results for EARLY-scored setups |
| **Pre-existing code confirmed at 29e1d4d** | YES — EARLY tier pre-exists; `potential_plan` nested under `directions` at line 23848 |
| **Open research needed** | Confirm which `/status` path exposes `potential_plan` and what a valid EARLY synthetic input looks like |

---

### V1-P4-004: ACTIVE TRADE State Explanation

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-004 |
| **Title** | Verify ACTIVE TRADE state explanation: active trade key present in `/status` when trade open |
| **Purpose** | Confirm the operator can see an active trade in Operator Mode |
| **Priority** | MEDIUM |
| **Implementation owner** | `active_trade_mgmt` block in `/status` at line 44632 via `_active_trade_mgmt_block(a, ...)`. Also: `active_trade` is a top-level key of `full_analysis()` result (line 21538, 26368) |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `full_analysis()`, `active_trade_for()`, `set_active_trade()`, `_active_trade_mgmt_block()`, `_build_status_payload()` |
| **Interfaces touched** | Manager (read-only — read `ACTIVE_TRADES_BY_INST`) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change `ACTIVE_TRADES_BY_INST`, execution path, or trade lifecycle |
| **Required validation** | Inject synthetic active trade via `set_active_trade()` → assert `full_analysis()["active_trade"]` is non-None → assert `active_trade_mgmt` in `/status` is populated → restore state |
| **Completion criteria** | Runtime test: active trade injection → `/status` reflects it → cleanup |
| **Implementation risk** | MEDIUM — `ACTIVE_TRADES_BY_INST` is a real global store; tests must restore state. Note: the roadmap says "ACTIVE_TRADES_BY_INST key present in /status" but this key is not directly in `_build_status_payload()` — it is surfaced via `active_trade_mgmt`. Clarification needed during implementation |
| **Pre-existing code confirmed at 29e1d4d** | YES — `active_trade_mgmt` and `active_trade_for()` pre-exist |
| **Note on RBTM** | The roadmap notes (line 2205) that V1-P4-004 will verify whether the `right_brain` advisory block is present in `/status` during an active trade. This is a gap to check — `right_brain_trade_management` is in `/status` at line 44674 but it is flag-gated on `RIGHT_BRAIN_TRADE_MANAGEMENT_ENABLED` |

---

### V1-P4-005: THESIS_INVALIDATED State

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-005 |
| **Title** | Verify THESIS_INVALIDATED state: invalidating event type exposed in result |
| **Purpose** | Confirm the operator sees why a thesis was invalidated (opposite confirmation or stop breach) |
| **Priority** | MEDIUM |
| **Implementation owner** | `thesis_status` field inside trade management check at line 34422/34435; `thesis` block from `_apply_thesis()` |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `_apply_thesis()`, `compute_manual_trade_management()`, `_build_status_payload()` |
| **Interfaces touched** | Expert (read-only thesis snapshot) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change thesis invalidation detection logic |
| **Required validation** | (a) Verify `thesis_status` field exists in the trade management output when a synthetic INVALID thesis is injected; (b) Verify `thesis` block in `/status` exposes reason codes when invalidated |
| **Completion criteria** | Runtime test: inject INVALID thesis state → verify `thesis` block reflects invalidation |
| **Implementation risk** | HIGH — `thesis_invalidated` is NOT a top-level `/status` key. It is: (1) a check inside `compute_manual_trade_management()` at line 34422 (trade management health check), and (2) exposed via `result["thesis"]["status"]` in the thesis hysteresis snapshot. The "invalidating event type" the roadmap requires may map to `result["thesis"]` fields rather than a dedicated top-level key. This needs investigation during implementation |
| **Pre-existing code confirmed at 29e1d4d** | YES (thesis hysteresis block) — but top-level `thesis_invalidated` field may not exist |
| **Open research needed** | Confirm which field path exposes "invalidating event type" in `/status`. Candidates: `result["thesis"]["reason"]`, `result["thesis"]["status"]`, or the Active Thinking overlay `thesis_status` |

---

### V1-P4-006: VETO_ACTIVE State

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-006 |
| **Title** | Verify VETO_ACTIVE state: analyst veto reason exposed in `analyst` block |
| **Purpose** | Confirm the operator can see why a READY-scoring setup was demoted by an analyst veto |
| **Priority** | MEDIUM |
| **Implementation owner** | `analyst` block in `full_analysis()` result; `analyst["veto_would_fire"]` boolean; `_build_status_payload()` line 44547 (`"analyst": a.get("analyst")`) |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `full_analysis()`, `_build_status_payload()` |
| **Interfaces touched** | Expert (read-only), Partner (read-only) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change analyst veto logic |
| **Required validation** | Assert `analyst` block present in `/status`; assert `analyst["veto_would_fire"]` is a bool; assert `analyst` block contains the fields required to explain the veto reason to the operator |
| **Completion criteria** | Runtime test: `analyst` block in `/status` is a dict with `veto_would_fire` bool |
| **Implementation risk** | LOW — `analyst` block is always present in `/status` at line 44547; `veto_would_fire` is always a bool (several stubs confirm `"veto_would_fire": False`) |
| **Pre-existing code confirmed at 29e1d4d** | YES |

---

### V1-P4-007: MARKET_CLOSED State

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-007 |
| **Title** | Verify MARKET_CLOSED state: `verdict="WAIT"` + `strict_reason` contains market-closed signal + session info present |
| **Purpose** | Confirm the operator sees why no signal is produced outside market hours |
| **Priority** | HIGH |
| **Implementation owner** | `market_session_status()` (line 4274), closed-override block in `full_analysis()`, `market_status`/`market_open`/`next_open`/`market_reason` keys in `/status` at lines 44609-44612 |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `market_session_status()`, `full_analysis()`, `_build_status_payload()` |
| **Interfaces touched** | Expert (read-only) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change session status logic |
| **Required validation** | Assert `market_status`, `market_open`, `next_open`, `market_reason` all present in `/status`; assert `market_open` is False in test env (always market-closed); assert `verdict` is WAIT; assert `strict_reason` non-empty |
| **Completion criteria** | 5 fields verified in `/status` JSON covering the full market-closed explanation |
| **Implementation risk** | LOW — test environment is always market-closed; all required fields confirmed in `_build_status_payload()` |
| **Pre-existing code confirmed at 29e1d4d** | YES |

---

### V1-P4-008: Partner Failure Fallback

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-008 |
| **Title** | Write Partner-failure fallback test: simulate `compute_main_brain()` exception → neutral stubs in `/status`, not 500 |
| **Purpose** | Verify that Partner failure never propagates to a 500 on `/status` |
| **Priority** | HIGH |
| **Implementation owner** | `compute_main_brain()` wrapped in try/except at line 24483-24486: `result["main_brain"] = _main_brain_neutral("Main Brain unavailable (%s).")` on exception |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `compute_main_brain()` (mocked to raise), `full_analysis()`, `/status` Flask endpoint |
| **Interfaces touched** | Partner (fault injection only — no logic change) |
| **Runtime behavior changes** | NONE — tests mock only; production behavior unchanged |
| **Prohibited changes** | Must not change Partner compute path or fail-open handling |
| **Required validation** | `unittest.mock.patch("app.compute_main_brain", side_effect=RuntimeError("injection"))` → `/status` returns HTTP 200 → `main_brain` block is a dict (neutral stub) → `main_brain_voice` is a non-empty string |
| **Completion criteria** | Partner exception → HTTP 200 (not 500); `main_brain` key present in payload; `main_brain_voice` is a string |
| **Implementation risk** | LOW — fail-open try/except confirmed at line 24483-24486; architecture guarantees ARCH §6 behavior. Pattern is identical to the V1-P6-006 tests in `test_v1_interface_versions.py` (already a model) |
| **Pre-existing code confirmed at 29e1d4d** | YES |

---

### V1-P4-009: Operator Mode DIAGNOSTIC Audit

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-009 |
| **Title** | Audit Operator Mode for DIAGNOSTIC-tier content (panel audit — no code change if clean) |
| **Purpose** | Verify acceptance criterion 3.2: Operator Mode does not display per-gate PASS/FAIL tables, eval metrics, raw alert history, or any DIAGNOSTIC-tier content visible only in Engineering View |
| **Priority** | CRITICAL |
| **Implementation owner** | Dashboard HTML/JS in `app.py` (inline HTML template); panel tier classification system (`data-adv` attribute; "Advanced" declutter gate) |
| **Expected files** | `test_phase4_operator_explanation.py` (new) + possible `app.py` correction if audit finds a violation |
| **Expected functions** | Dashboard `_build_dashboard_html()` or equivalent; panel tier labels in dashboard source |
| **Interfaces touched** | None (display-only audit) |
| **Runtime behavior changes** | POSSIBLE SMALL CHANGE — if DIAGNOSTIC panel(s) found in Operator Mode tier, a CSS/HTML correction is authorized |
| **Prohibited changes** | Must not change: gate logic, edge scoring, verdict production, execution, any data flow |
| **Required validation** | (a) `node --check` the served dashboard `<script>` passes; (b) panel inventory: confirm no DIAGNOSTIC-labeled panels are rendered in the Operator Mode section without the `data-adv` guard; (c) per-gate PASS/FAIL table, eval metrics, and raw alert history must be in Engineering View only |
| **Completion criteria** | Audit report documents each DIAGNOSTIC panel and its section membership. If violations found: corrected + verified. If clean: documented. |
| **Implementation risk** | HIGH — this is the most complex audit task. Dashboard is a single 60,000+ char HTML string. Violations would require careful CSS/JS-only fixes that are strictly additive and leave the data flow unchanged. Any `app.py` change for this task must run the full regression suite before commit |
| **Pre-existing code confirmed at 29e1d4d** | N/A — this is an audit of current state |
| **Node check guard** | After any dashboard HTML/JS change: `node --check` the served `<script>` (dual_sim + breakout_mode smoke scripts do this automatically) |

---

### V1-P4-010: /decision-trace in Engineering View

| Field | Detail |
|---|---|
| **Task ID** | V1-P4-010 |
| **Title** | Verify `/decision-trace` accessible from Engineering View (owner auth required) |
| **Purpose** | Confirm the decision timeline endpoint is accessible to authenticated operators and returns the correct schema |
| **Priority** | LOW |
| **Implementation owner** | `/decision-trace` endpoint at line 43788; `DECISION_TRACE_SHADOW_ENABLED` flag (line 611); Express proxy whitelist |
| **Expected files** | `test_phase4_operator_explanation.py` (new) |
| **Expected functions** | `decision_trace_endpoint()`, Flask test client |
| **Interfaces touched** | Expert (decision trace is display/diagnostics-only) |
| **Runtime behavior changes** | NONE |
| **Prohibited changes** | Must not change `/decision-trace` behavior, auth, or flag semantics |
| **Required validation** | (a) Endpoint returns HTTP 200 (Phase 3 already confirmed); (b) flag-OFF contract `{enabled: false, traces: {}}` (Phase 3 already confirmed); (c) endpoint is NOT in OPEN_PATHS (requires auth via Express proxy) — verify by reading the proxy whitelist rather than a live HTTP call |
| **Completion criteria** | Auth model documented; flag-ON/OFF contracts verified (may reference Phase 3 P3-006 tests as implementation-inherited) |
| **Implementation risk** | LOW — Phase 3 (V1-P3-006) already wrote 5 tests covering the /decision-trace contract. Phase 4 may extend or reference these. Auth is via Express proxy (NOT in OPEN_PATHS), not a Flask `@owner_required` decorator — this is the documented behavior (line 43790 comment) |
| **Pre-existing code confirmed at 29e1d4d** | YES |
| **Phase 3 overlap note** | V1-P3-006 already verified the `/decision-trace` flag-OFF and flag-ON contracts with 5 runtime tests. V1-P4-010 adds the auth-model verification layer on top |

---

## 5. Dependency Graph

```
Phase 3 COMPLETE (aa7ee5f / ee808db)
    │
    ├─► V1-P4-007 (MARKET_CLOSED)    ─┐
    ├─► V1-P4-001 (WAIT)              │ Independent group — both are
    ├─► V1-P4-006 (VETO_ACTIVE)       │ guaranteed states in test env.
    ├─► V1-P4-008 (Partner fallback)  │ Can run in parallel.
    ├─► V1-P4-002 (READY fields)      │
    ├─► V1-P4-003 (EARLY)             │ Require research (see §10)
    ├─► V1-P4-004 (ACTIVE TRADE)      │
    └─► V1-P4-005 (THESIS_INVALIDATED)┘
            │
            ▼
    V1-P4-009 (Operator Mode audit) — depends on V1-P4-001 through V1-P4-007 per roadmap
            │
            ▼
    V1-P4-010 (/decision-trace) — depends on V1-P4-009 per roadmap
```

**Hidden dependency:** V1-P4-009 requires a complete panel-tier inventory, which is easier after the seven state verifications have confirmed what data is available to Operator Mode. V1-P4-008 is independent of all state verification tasks.

**Pre-implementation research dependency:** V1-P4-003, V1-P4-004, V1-P4-005 all require the researcher to confirm the exact `/status` field path before writing the test. This research should happen before the implementation batch begins, not during.

---

## 6. File Impact Matrix

### Files Expected to Change

| File | Reason | Owning subsystem | Est. change size | Runtime risk | Interface risk | Regression risk |
|---|---|---|---|---|---|---|
| `artifacts/tradingview-webhook/test_phase4_operator_explanation.py` | New test file covering V1-P4-001 through V1-P4-010 | Phase 4 verification | ~700–900 lines | NONE — tests only | NONE | LOW — additive |
| `V1_PHASE_4_VALIDATION.md` | New validation document | Documentation | ~400 lines | NONE | NONE | NONE |
| `artifacts/tradingview-webhook/app.py` | **Conditional only** — if V1-P4-009 audit finds DIAGNOSTIC content in Operator Mode tier, a CSS/HTML correction is authorized. If audit is clean, `app.py` is NOT changed | Dashboard display | Small — CSS/HTML only, no logic | LOW (display-only) | NONE | LOW (covered by dual_sim + breakout_mode node-check smokes) |

### Files Expected NOT to Change

| File | Reason |
|---|---|
| `artifacts/tradingview-webhook/app.py` (gate logic) | Phase 4 must not change gate decisions, verdict production, or Partner compute path |
| `artifacts/tradingview-webhook/left_brain_market_intelligence.py` | Phase 4 does not touch Left Brain thesis or MI |
| `artifacts/tradingview-webhook/test_v1_interface_versions.py` | Phase 1 contract tests — no Phase 4 modifications needed |
| `artifacts/tradingview-webhook/test_phase2_market_data_reliability.py` | Phase 2 tests — no Phase 4 modifications |
| `artifacts/tradingview-webhook/test_phase3_thesis_verdict_pipeline.py` | Phase 3 tests — no Phase 4 modifications |
| `artifacts/tradingview-webhook/checks/run_phase2_smoke.sh` | Phase 2 smoke — no Phase 4 modifications |
| Golden files (any `*_golden_*` or baseline files) | Phase 4 must not change any behavioral baseline |
| `.local/state/check_*.sh` (primary regressions) | Phase 4 must not change any primary regression |

---

## 7. Interface Impact Matrix

| Interface | Impact | Detail |
|---|---|---|
| **Left Brain** | UNCHANGED | V1-P4 does not touch Left Brain thesis, MI, or OUTLOOK_SHIFT. Left Brain output is read-only by Phase 4 tests. |
| **Expert** | UNCHANGED | Gate logic, edge scoring, verdict production, `evaluate_strict_setup()`, `cfg_for()`, `is_actionable()` — all read-only in Phase 4 tests. `strict_reason` and `gate_debug` verified but not modified. |
| **Partner** | UNCHANGED (test mock only) | V1-P4-008 mocks `compute_main_brain()` with `unittest.mock.patch()` in a test — the production function is not changed. |
| **Manager** | UNCHANGED | V1-P4-004 reads `ACTIVE_TRADES_BY_INST` via `active_trade_for()` and injects a synthetic trade for test purposes only. No execution logic modified. State is restored after test. |
| **Coach** | UNCHANGED | Phase 4 does not touch learning, weights, thesis resolution, or Coach boundaries. |
| **Journal** | UNCHANGED | Phase 4 does not touch trade capture, Discord sends, or `strategy_trades`. |
| **Execution Gateway** | UNCHANGED | Phase 4 does not touch order routing, broker payloads, or execution providers. |

---

## 8. Validation Strategy

### New Tests (all in `test_phase4_operator_explanation.py`)

Estimated 35–45 runtime behavioral tests, organized by task.

| Task | Estimated tests | Test type |
|---|---|---|
| V1-P4-001 (WAIT state) | 3–4 | Integration — `/status` HTTP client + `full_analysis()` call |
| V1-P4-002 (READY fields) | 4–5 | Integration — field presence + type verification in `/status` |
| V1-P4-003 (EARLY state) | 3–4 | Isolated unit + integration — `alert_level` key in `/status`; `potential_plan` schema via `directions` |
| V1-P4-004 (ACTIVE TRADE) | 4–5 | Integration — `set_active_trade()` inject + verify + restore |
| V1-P4-005 (THESIS_INVALIDATED) | 3–4 | Runtime behavioral — thesis block state injection + verify |
| V1-P4-006 (VETO_ACTIVE) | 3–4 | Integration — `analyst.veto_would_fire` in `/status` |
| V1-P4-007 (MARKET_CLOSED) | 4–5 | Integration — 5 session fields verified in `/status` |
| V1-P4-008 (Partner fallback) | 4–5 | Integration — `unittest.mock.patch` + `/status` HTTP 200 |
| V1-P4-009 (Operator Mode audit) | 3–4 | Static + runtime — `node --check` dashboard JS; panel tier assertions |
| V1-P4-010 (/decision-trace) | 2–3 | Integration — flag-OFF/ON contracts; auth model documentation |

### Regression Tests (all existing — must pass unchanged)

| Suite | Current count | Expected count |
|---|---|---|
| `test_phase4_operator_explanation.py` | 0 | 35–45 (new) |
| `test_v1_interface_versions.py` | 85 | 85 (unchanged) |
| `test_phase3_thesis_verdict_pipeline.py` | 60 | 60 (unchanged) |
| `test_phase2_market_data_reliability.py` | 45 | 45 (unchanged) |
| Phase 2 smoke (`run_phase2_smoke.sh`) | 8/8 | 8/8 |
| parity | PASS | PASS |
| scalp_golden | PASS | PASS |
| dual_sim | PASS | PASS |
| breakout_mode | PASS | PASS |

### Additional Checks

- `py_compile` on `app.py` must pass
- `git diff --check` must be CLEAN before any commit
- `node --check` on the served dashboard `<script>` (V1-P4-009)

### Evidence Required to Declare Completion

For each Phase 4 task: at least one runtime test that calls a real function or real HTTP endpoint and asserts a non-trivial return value. Static-only checks (grep, AST, `inspect.getsource()`) are not sufficient as primary evidence for behavioral requirements.

---

## 9. Recommended Implementation Order

### Step 1: Pre-Implementation Research (before writing any test)

Resolve the four open research questions in the risk register:
1. Confirm which `/status` path exposes `potential_plan` for EARLY state (V1-P4-003)
2. Confirm what field in `/status` is the authoritative "active trade indicator" (V1-P4-004)
3. Confirm which field path exposes "invalidating event type" (V1-P4-005)
4. Confirm the Express proxy auth model for `/decision-trace` (V1-P4-010)

### Step 2: Group A — Guaranteed States (implement first, parallel batch)

| Task | Difficulty | Regression risk | Rollback |
|---|---|---|---|
| V1-P4-007 MARKET_CLOSED | LOW | NONE | Delete test |
| V1-P4-001 WAIT | LOW | NONE | Delete test |
| V1-P4-006 VETO_ACTIVE | LOW | NONE | Delete test |
| V1-P4-008 Partner fallback | LOW | NONE | Delete test |

All four are guaranteed states in the test environment. These should be written together and run together before proceeding.

**Validation point:** 14–18 new tests pass + 190 existing tests unchanged.

### Step 3: Group B — Field Verification (depends on Step 1 research)

| Task | Difficulty | Regression risk | Rollback |
|---|---|---|---|
| V1-P4-002 READY fields | MEDIUM | NONE | Delete test |
| V1-P4-003 EARLY state | MEDIUM | NONE | Delete test |
| V1-P4-004 ACTIVE TRADE | MEDIUM | LOW (state injection with cleanup) | Delete test + verify store restored |
| V1-P4-005 THESIS_INVALIDATED | MEDIUM-HIGH | LOW | Delete test |

**Validation point:** All new Group B tests pass + all Group A tests still pass + 190 existing tests unchanged.

### Step 4: Group C — Audit and Timeline

| Task | Difficulty | Regression risk | Rollback |
|---|---|---|---|
| V1-P4-009 Operator Mode audit | HIGH | LOW (display-only) | Revert CSS/HTML only |
| V1-P4-010 /decision-trace auth | LOW | NONE | Delete test |

**Validation point:** Full regression suite (all tests + 4 primaries + smoke). node --check on dashboard JS.

### Step 5: Full Regression + Validation Document + Commit

- Run all tests + 4 primaries + Phase 2 smoke
- Write `V1_PHASE_4_VALIDATION.md`
- Commit: `V1-P4 Operator Explanation and Decision Timeline`

### Natural Checkpoints

| After step | Checkpoint action |
|---|---|
| Step 1 (research) | Document research findings before writing any test |
| Step 2 (Group A) | Run full regression; confirm 0 regressions before Group B |
| Step 3 (Group B) | Run full regression; confirm 0 regressions before audit |
| Step 4 (audit) | `node --check` dashboard JS; full regression |
| Step 5 (final) | Freeze and report |

---

## 10. Risk Register

### High Risk

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **V1-P4-005: `thesis_invalidated` not a top-level `/status` field** | HIGH | MEDIUM | Research the exact field path before writing the test. Candidates: `result["thesis"]["status"]`, `result["thesis"]["reason"]`, or `active_thinking["thesis_status"]`. Test whichever path is authoritative. Update roadmap task description in validation doc if the field path differs from the verbal description. |
| **V1-P4-003: `potential_plan` nested, not top-level** | HIGH | MEDIUM | `potential_plan` is confirmed nested inside `result["directions"][direction]["potential_plan"]` (line 23848). `/status` exposes `directions` wholesale (line 44508). Tests must navigate the nested path: `/status["directions"]["Long"]["potential_plan"]`. Test the schema of the nested dict, not a top-level key. |
| **V1-P4-009: DIAGNOSTIC content found in Operator Mode** | MEDIUM | HIGH | If found, authorized correction is CSS/HTML only (additive hide or section re-assignment). Change must be confined to the dashboard template string in `app.py`. Must not change any data key, data flow, or Flask route. Full regression required after any `app.py` change. |

### Medium Risk

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **V1-P4-004: `set_active_trade()` state not fully restored** | MEDIUM | MEDIUM | Wrap test in try/finally + call `clear_active_trade(inst, opened_at)` in finally block. Confirm `ACTIVE_TRADES_BY_INST[inst]` is None after test. |
| **V1-P4-002: READY fields absent in specific fallback paths** | LOW | MEDIUM | Fields `main_brain_voice`, `grade`, `edge_score`, `trade_plan` are confirmed in `_build_status_payload()` regardless of verdict. Test as "always present fields" not "READY-only fields." |
| **V1-P4-010: Express proxy auth is not testable via Flask test client** | MEDIUM | LOW | The Flask test client bypasses Express. Auth via Express proxy cannot be unit-tested at the Flask layer. Mitigation: verify the endpoint is NOT in `OPEN_PATHS` (grep) and document auth as "proxy-enforced per line 43790 comment." This is structural verification, not runtime auth simulation — acceptable per roadmap since the task is labeled LOW. |

### Low Risk

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **V1-P4-009 `node --check` failure from dashboard JS** | LOW | MEDIUM | Node-check runs as part of dual_sim and breakout_mode smoke — any JS syntax error would already be caught. Run smokes before commit. |
| **V1-P4-008 mock isolation leak** | LOW | LOW | Use `unittest.mock.patch` as context manager. Verify `full_analysis()` runs normally after mock exits. |
| **New test file imports fail** | LOW | LOW | Follow the same `sys.path.insert(0, ...)` + `import app` pattern established in Phase 2 and Phase 3 test files. |
| **State injection race in V1-P4-004** | LOW | LOW | Tests are single-threaded. `ACTIVE_TRADES_LOCK` is only needed for concurrent writes. Inject via `set_active_trade()` which acquires the lock correctly. |

### Potential Architecture Conflicts

None identified. Phase 4 is purely additive verification. The only authorized non-test change (V1-P4-009 CSS/HTML fix) is explicitly described as display-only with no data flow effect.

### Potential Regression Risks

- V1-P4-004 state injection: if test fails mid-execution, `ACTIVE_TRADES_BY_INST` may be left with a synthetic trade. Try/finally guard is mandatory.
- V1-P4-009 dashboard HTML change: any change to the dashboard template string risks the JS escape bug (documented in `dashboard-js-string-escape-bug.md` memory). Every app.py dashboard change must be followed by `node --check` on the served `<script>`.

### Potential Testing Gaps

- READY state cannot be forced in the test environment (market always closed). V1-P4-002 tests must verify that READY-explanation fields are always present, not only on READY. This is architecturally correct — the fields are populated unconditionally.
- EARLY state cannot be forced without state injection. V1-P4-003 must use the `directions` block schema approach rather than live EARLY verdict production.

---

## 11. Execution Contract

### Authorized Runtime Changes

1. New test file: `artifacts/tradingview-webhook/test_phase4_operator_explanation.py`
2. New validation document: `V1_PHASE_4_VALIDATION.md`
3. **Conditional:** `app.py` dashboard HTML/JS — CSS/HTML only correction to remove DIAGNOSTIC content from Operator Mode tier, if and only if V1-P4-009 audit finds a violation. No logic, data key, Flask route, or data flow change is authorized.

### Explicitly Prohibited Changes

- Gate logic (`evaluate_strict_setup()`, `cfg_for()`, GATE_REQUIRE_* config)
- Edge scoring (`_analysis_edge_breakdown()`, EDGE_COMPONENTS)
- Verdict production (`full_analysis()` return values)
- Partner compute path (`compute_main_brain()`, `_mb_orchestrate()`, `compute_main_brain_voice()`)
- Execution gateway (`execute_trade_gateway()`, broker payloads)
- Journal (`strategy_trades` INSERT logic, Discord send logic)
- Learning engine (weights, eligibility, thresholds)
- Thesis hysteresis (`_apply_thesis()`, `_THESIS_HOLD_THRESHOLD`)
- Left Brain (`compute_left_brain_thesis()`, `_detect_significant_changes()`)
- Database schema (no new tables, no DDL, no ALTER)
- Any golden baseline file
- Any primary regression workflow (`.local/state/check_*.sh`)
- `OPEN_PATHS` list (cannot add `/decision-trace` — it is correctly not open)

### Authorized Files

| File | Authorization |
|---|---|
| `artifacts/tradingview-webhook/test_phase4_operator_explanation.py` | Full — new file |
| `V1_PHASE_4_VALIDATION.md` | Full — new file |
| `artifacts/tradingview-webhook/app.py` | Conditional CSS/HTML only — V1-P4-009 fix if needed |

### Prohibited Files (no changes)

All other files. Specifically:
- `artifacts/tradingview-webhook/app.py` (logic sections)
- `artifacts/tradingview-webhook/left_brain_market_intelligence.py`
- `artifacts/tradingview-webhook/test_v1_interface_versions.py`
- `artifacts/tradingview-webhook/test_phase2_market_data_reliability.py`
- `artifacts/tradingview-webhook/test_phase3_thesis_verdict_pipeline.py`
- `artifacts/tradingview-webhook/checks/run_phase2_smoke.sh`
- `.local/state/check_*.sh`

### Required Regression Suite (must pass before commit)

```
python3 -m pytest artifacts/tradingview-webhook/test_phase4_operator_explanation.py -v
python3 -m pytest artifacts/tradingview-webhook/test_v1_interface_versions.py artifacts/tradingview-webhook/test_phase3_thesis_verdict_pipeline.py artifacts/tradingview-webhook/test_phase2_market_data_reliability.py -q
bash .local/state/check_parity.sh
bash .local/state/check_scalp_golden.sh
bash .local/state/check_dual_sim.sh
bash .local/state/check_breakout_mode.sh
bash artifacts/tradingview-webhook/checks/run_phase2_smoke.sh
python3 -c "import py_compile; py_compile.compile('artifacts/tradingview-webhook/app.py', doraise=True)"
git diff --check HEAD
```

### Required Documentation

- `V1_PHASE_4_VALIDATION.md` with 12 sections (identical structure to Phase 3 validation document)
- Sections must include: Baseline State, Scope, Task-by-Task Results, Architecture Compliance, Test Evidence, Behavioral Comparison, File Inventory, Deployment Status, Final Phase Status, Pre-Implementation Research Findings

### Required Validation Evidence

For every Phase 4 task: at minimum one runtime behavioral assertion (real function call, real HTTP endpoint, real return value). No task may rely solely on grep, AST, or `inspect.getsource()` as its completion evidence.

### Required Commit Strategy

One commit: `V1-P4 Operator Explanation and Decision Timeline`

If V1-P4-009 requires an `app.py` CSS/HTML fix: the fix must be in the same commit as the tests. Do not create a separate "fix" commit.

If only the validation document changes (no code changes): one documentation commit per the brief's commit control rules.

### Stop Conditions

Stop and report without committing if:
- Any primary regression (`parity`, `scalp_golden`, `dual_sim`, `breakout_mode`) fails after any change
- Any of the 190 existing tests fail after adding new Phase 4 tests
- `git diff --check` is not CLEAN
- `py_compile` fails
- `node --check` fails on the served dashboard `<script>`
- V1-P4-009 reveals a DIAGNOSTIC violation requiring a logic change (display-only CSS/HTML is authorized; logic is not)
- Any Phase 5 work is inadvertently in scope

---

## 12. Completion Checklist

```
Pre-implementation research:
  [ ] V1-P4-003: potential_plan path in /status confirmed (nested in directions)
  [ ] V1-P4-004: active trade indicator field in /status confirmed
  [ ] V1-P4-005: thesis_invalidated field path confirmed
  [ ] V1-P4-010: Express proxy auth model confirmed (NOT in OPEN_PATHS)

Group A tests written and passing:
  [ ] V1-P4-007: 4–5 tests passing (MARKET_CLOSED — 5 session fields in /status)
  [ ] V1-P4-001: 3–4 tests passing (WAIT — strict_reason non-empty)
  [ ] V1-P4-006: 3–4 tests passing (VETO_ACTIVE — analyst.veto_would_fire bool)
  [ ] V1-P4-008: 4–5 tests passing (Partner fallback — HTTP 200 + neutral stubs)
  [ ] Full regression after Group A: 190 existing tests pass

Group B tests written and passing:
  [ ] V1-P4-002: 4–5 tests passing (READY fields — 4 keys present + typed)
  [ ] V1-P4-003: 3–4 tests passing (EARLY — alert_level key + potential_plan schema)
  [ ] V1-P4-004: 4–5 tests passing (ACTIVE TRADE — inject + verify + restore)
  [ ] V1-P4-005: 3–4 tests passing (THESIS_INVALIDATED — thesis block reflects invalidation)
  [ ] Full regression after Group B: 190 existing tests still pass

Group C complete:
  [ ] V1-P4-009: Operator Mode audit documented; violation found/not found; fix applied if needed
  [ ] V1-P4-010: /decision-trace auth model verified; flag contracts confirmed
  [ ] node --check on served dashboard <script>
  [ ] Full regression: all new Phase 4 tests pass + 190 existing pass

Documentation:
  [ ] V1_PHASE_4_VALIDATION.md written with all required sections
  [ ] py_compile PASS
  [ ] git diff --check CLEAN

Commit:
  [ ] V1-P4 Operator Explanation and Decision Timeline

NOT done:
  [ ] Phase 5 not begun
  [ ] Not deployed
  [ ] Not published
```
