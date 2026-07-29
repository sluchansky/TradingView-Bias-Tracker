# V1 Manager Interface + Coach Interface — Validation Report

**Date:** 2026-07-29  
**Branch:** `polish-v1`  
**Baseline commit:** `e90c5d8` (platform auto-commit atop corrective `36bd6e1`)  
**Batch scope:** V1-P1-004 Manager Interface v1 + V1-P1-007 Coach Interface v1

---

## 1. Pre-Work Baseline Freeze

| Item | Value |
|---|---|
| Branch | `polish-v1` |
| HEAD at start | `e90c5d8` |
| Ancestor confirmed | `36bd6e1` (corrective validation) |
| Working tree | Clean (only untracked: uploaded instruction file) |
| Previous task result | 27/27 tests passing; canonical interfaces absent; isolation invariants confirmed |

---

## 2. Stage 1 — Source Mapping (all 9 fields)

### Manager Interface fields

| Field | Authoritative source | Read-only? | Available in `full_analysis()`? | Unavailable rep |
|---|---|---|---|---|
| `gateway_debug` | `result.get("gate_debug")` — set at `result["gate_debug"] = strict.get("gate_debug")` in `full_analysis()` | ✓ | ✓ always | `{}` |
| `active_trade` | `active_trade_snapshot().get(inst)` — reads `ACTIVE_TRADES_BY_INST` under RLock | ✓ | ✓ (direct read) | `None` |
| `managed_trade` | `list(MANAGED_TRADES_BY_KEY.values())` scan for open entry matching instrument — lock-free list snapshot | ✓ | ✓ (direct read) | `None` |
| `training_gate` | `training_mode_enabled()` — pure env-var read of `TRAINING_MODE_ENABLED_RAW` | ✓ | ✓ always | `{"enabled": False}` |
| `auto_trade_enabled` | `{a: auto_trade_enabled(a) for a in ASSETS}` — reads `AUTO_TRADE` dict under `AUTO_TRADE_LOCK` | ✓ | ✓ always | `{}` |

### Coach Interface fields

| Field | Authoritative source | Read-only? | Available in `full_analysis()`? | Unavailable rep |
|---|---|---|---|---|
| `weight_updated` | `LEARNING_ANALYTICS.get("ready", False)` under `LEARNING_LOCK` — `True` after `_recompute_learning()` sets `ready=True` | ✓ | ✓ always | `False` |
| `thesis_resolved` | `bool(THESIS_TRACKER_DB_READY)` — `True` after `_check_thesis_tracker_db_ready()` confirms `thesis_snapshots` table accessible | ✓ | ✓ always | `False` |
| `learning_influence` | `result["learning_score_influence"]["Long/Short"]["delta"]` — active-direction delta from `_resolve_learning_score_influence()`; only one direction is non-zero | ✓ | ✓ (computed per-analysis) | `0.0` |
| `rule_engine_eligibility` | `_check_learning_eligibility(inst, mode)[0]` — reads `LEARNING_ELIGIBILITY` cache under `LEARNING_ELIGIBILITY_LOCK` | ✓ | ✓ (cache read) | `"LIVE_ELIGIBLE"` (fail-open) |

**Stop conditions encountered:** None. All 9 fields have confirmed read-only authoritative sources with no DB writes, no gateway calls, and no side effects.

---

## 3. Stage 2 — Builder Implementations

### `build_manager_interface(result, instrument=None)` — added to `app.py`

Location: inserted immediately before `def full_analysis(...)` (~line 22780).

```
Guaranteed output keys: gateway_debug, active_trade, managed_trade,
                        training_gate, auto_trade_enabled, _version
_version: "v1" — unconditional
```

Fail-open wrapper catches any unexpected exception and returns neutral stubs with `_version: "v1"` still present.

### `build_coach_interface(result, instrument=None, mode=None)` — added to `app.py`

Location: immediately after `build_manager_interface`, before `def full_analysis(...)`.

```
Guaranteed output keys: weight_updated, thesis_resolved, learning_influence,
                        rule_engine_eligibility, _version
_version: "v1" — unconditional
```

Fail-open wrapper catches any unexpected exception and returns neutral stubs with `_version: "v1"` still present.

---

## 4. Stage 3 — Integration

**Integration point:** end of `full_analysis()`, immediately before the existing Expert version tag:

```python
# V1 Manager and Coach Interface objects — additive, read-only status objects.
result["manager"] = build_manager_interface(result, active_ticker)
result["coach"]   = build_coach_interface(result, active_ticker, TRADING_MODE)

# V1 Expert Interface version field — additive, no behavior change.
result["_version"] = "v1"
return result
```

**`/status` whitelist:** `"manager"` and `"coach"` added to `_build_status_payload()` alongside `learning_rule_engine`.

**Not added to:** broker payloads, database records, gateway audit log, Discord embeds, journal cards, or any money-path dict.

**Strict invariants preserved:**
- `_active_trade_mgmt_block()` — display helper, unchanged, no `_version`
- `result["learning_score_influence"]` — edge-scoring modifier block, unchanged, no `_version`
- Gate, scoring, execution, and safety logic — zero changes

---

## 5. Stage 4 — Test Results

**File:** `artifacts/tradingview-webhook/test_v1_interface_versions.py`  
**Total checks:** 49 (was 27 before this batch)  
**New checks added:** 22 (12 Manager + 10 Coach runtime; 2 cross-matrix entries updated)

```
  PASS  test_coach_build_returns_dict
  PASS  test_coach_canonical_fields_absent
  PASS  test_coach_in_full_analysis
  PASS  test_coach_learning_influence_is_float
  PASS  test_coach_learning_influence_range
  PASS  test_coach_lsi_existing_fields_intact
  PASS  test_coach_lsi_no_version
  PASS  test_coach_required_fields
  PASS  test_coach_rule_engine_eligibility_valid
  PASS  test_coach_thesis_resolved_is_bool
  PASS  test_coach_version
  PASS  test_coach_version_serializes
  PASS  test_coach_version_type
  PASS  test_coach_weight_updated_is_bool
  PASS  test_cross_interface_version_matrix
  PASS  test_expert_required_fields
  PASS  test_expert_version
  PASS  test_expert_version_serializes
  PASS  test_expert_version_type
  PASS  test_gateway_manual_required_version_in_source
  PASS  test_gateway_sent_version_in_source
  PASS  test_gateway_simulated_version_in_source
  PASS  test_gateway_version_count
  PASS  test_gateway_version_not_in_broker_payload
  PASS  test_journal_version_before_return
  PASS  test_journal_version_in_source
  PASS  test_journal_version_single_seam
  PASS  test_lb_compute_thesis_degraded_version
  PASS  test_lb_compute_thesis_error_path_version
  PASS  test_lb_neutral_thesis_required_fields
  PASS  test_lb_neutral_thesis_version
  PASS  test_lb_version_serializes
  PASS  test_manager_active_trade_type
  PASS  test_manager_atm_block_no_version
  PASS  test_manager_auto_trade_enabled_is_dict
  PASS  test_manager_build_returns_dict
  PASS  test_manager_canonical_fields_absent_from_atm_block
  PASS  test_manager_gateway_debug_is_dict
  PASS  test_manager_in_full_analysis
  PASS  test_manager_managed_trade_type
  PASS  test_manager_required_fields
  PASS  test_manager_training_gate_has_enabled
  PASS  test_manager_version
  PASS  test_manager_version_serializes
  PASS  test_manager_version_type
  PASS  test_partner_compute_version
  PASS  test_partner_neutral_required_fields
  PASS  test_partner_neutral_version
  PASS  test_partner_version_type

  TOTAL: 49 checks — 49 passed, 0 failed
```

---

## 6. Stage 5 — Regression Results

| Regression | Result |
|---|---|
| `check_parity.sh` | **PARITY OK** (registry/resolver identical to baseline) |
| `check_scalp_golden.sh` | **SCALP GOLDEN OK** (byte-identical to baseline) |
| `check_dual_sim.sh` | **DUAL-SIM SMOKE OK** (MODE=SCALP — fidelity + money-path isolation) |
| `check_breakout_mode.sh` | **BREAKOUT MODE SMOKE OK** |
| `python3 -m py_compile app.py` | **SYNTAX OK** |
| `git diff --check` | **DIFF CHECK OK** (no trailing whitespace / conflict markers) |

Zero new failures. All 4 primary regressions byte-identical to baselines.

---

## 7. Cross-Interface Version Matrix — Final State

| Interface | Version | Status | Builder / Seam |
|---|---|---|---|
| V1-P1-001 Expert | v1 | ✅ COMPLETE | `full_analysis()` result |
| V1-P1-002 Left Brain | v2 | ✅ COMPLETE | `_neutral_thesis()` + `compute_left_brain_thesis()` |
| V1-P1-003 Partner | v1 | ✅ COMPLETE | `compute_main_brain()` + `_main_brain_neutral()` |
| V1-P1-004 Manager | v1 | ✅ COMPLETE | `build_manager_interface()` ← **this batch** |
| V1-P1-005 Execution Gateway | v1 | ✅ COMPLETE | `execute_trade_gateway()` (3 success returns) |
| V1-P1-006 Journal | v1 | ✅ COMPLETE | `_build_card_entry()` entry dict |
| V1-P1-007 Coach | v1 | ✅ COMPLETE | `build_coach_interface()` ← **this batch** |

---

## 8. Architectural Isolation Confirmation

Both non-canonical objects verified unchanged and version-free:

| Object | Role | `_version` present? | Manager/Coach fields present? |
|---|---|---|---|
| `_active_trade_mgmt_block()` | Display helper: `{enabled, count, positions, updated_at}` | ✗ None — confirmed | ✗ None — confirmed |
| `result["learning_score_influence"]` | Edge-scoring modifier: `{enabled, armed, max_delta, meta, Long, Short}` | ✗ None — confirmed | ✗ None — confirmed |

The canonical `build_manager_interface()` and `build_coach_interface()` functions are the SOLE builders for their respective ARCH §7 contracts. No other object in the codebase carries these contracts.

---

## 9. Semantic Correctness Audit After 4e322c8

**Audit date:** 2026-07-29
**Commit audited:** `4e322c8` (Manager Interface v1 + Coach Interface v1 initial build)
**Correction commit:** `V1-P1 correct Manager and Coach contract semantics`

### Motivation

The initial build passed all 49 presence/type tests but two Coach field mappings were
semantically incorrect — they reported subsystem readiness rather than actual events.

### Findings

| Field | Original mapping | Problem | Corrected mapping |
|---|---|---|---|
| `weight_updated` | `LEARNING_ANALYTICS.get("ready", False)` | `ready = total_trades > 0` — measures whether trades exist in DB, NOT whether the `_recompute_learning()` function ran | `bool(LEARNING_ANALYTICS.get("updated_at"))` — `updated_at` is set at line ~12590 ONLY when `_recompute_learning()` completes successfully; absent at boot; never set by DB probes or readiness checks |
| `thesis_resolved` | `bool(THESIS_TRACKER_DB_READY)` | `THESIS_TRACKER_DB_READY` measures DB table accessibility, NOT whether a thesis resolution event occurred | `False` — the ARCH-defined value for "resolve did not run"; no global "last resolve ran" flag exists; no thesis resolution event occurs during `full_analysis()` (only at trade-close) |
| `active_trade` (Manager) | `at_snap.get(inst)` — live reference | `active_trade_snapshot()` shallow-copies the outer dict but inner trade dicts are shared references; consumers could mutate `ACTIVE_TRADES_BY_INST` | `dict(_at_raw)` — shallow copy prevents consumer mutation of global state |
| `managed_trade` (Manager) | `_mt` — live reference | Same as above for `MANAGED_TRADES_BY_KEY` | `dict(_mt)` — shallow copy |

### Sources Investigated

- `_recompute_learning()` body (lines 12374–12700): `updated_at` set at line 12590 inside the try body, after all DB reads and weight computation, before the atomic lock-swap. Boot default `{"enabled":…, "ready": False, "total_trades": 0}` has no `updated_at`. This is the authoritative "recompute ran" signal, distinct from readiness/enablement/sample-sufficiency.

- `THESIS_TRACKER_DB_READY` source (`_check_thesis_tracker_db_ready()`): boot-time DB probe — True = table accessible. No global "thesis resolution occurred" flag exists anywhere in the codebase. Resolution events are per-trade, per-row (`resolved_at` in `thesis_snapshots` table only).

- `_ls_dir_summary()` (line 23454–23465): `"delta": gd.get("learning_score_delta", 0)` — the actual ±15 adjustment applied during scoring. `learning_influence` mapping was already correct.

- `_check_learning_eligibility()`: pure `LEARNING_ELIGIBILITY_LOCK` read — no writes, no counters, no DB, no recompute. `rule_engine_eligibility` mapping was already correct.

- `active_trade_snapshot()` (line 206–209): `dict(ACTIVE_TRADES_BY_INST)` — confirmed shallow copy of outer dict only.

### ARCH §7 Contract Interpretation

Per the audit's explicit permission: *"If the authoritative event information only exists at trade-close time, the Coach interface may legitimately report unavailable during ordinary full_analysis()."*

`thesis_resolved = False` is correct — not an invented substitute but the ARCH-defined non-True value for a bool field where True means "resolve ran." During `full_analysis()` no resolve occurs.

`weight_updated = bool(LEARNING_ANALYTICS.get("updated_at"))` is correct — a real event signal (recompute completion) rather than a system-state proxy.

### V1-P1-007 Acceptance Criterion

ROADMAP line 1521 requires: `assert weight_updated, learning_influence, rule_engine_eligibility, _version present` — all 4 remain present. The correction changes the *semantic source* of `weight_updated`, not its presence or type.

### Test Count After Audit

| Suite | Before | After |
|---|---|---|
| Presence/type tests | 49 | 49 (unchanged) |
| Semantic proof tests | 0 | 21 new (Coach: 14, Manager: 7) |
| **Total** | **49** | **70** |

All 70 tests pass. All 4 primary regressions pass (parity, scalp_golden, dual_sim, breakout_mode).

### Semantic Tests Added (21)

**Coach (14):**
- `test_coach_learning_ready_does_not_imply_weight_updated` — `ready=True` without `updated_at` → `weight_updated=False`
- `test_coach_learning_enabled_does_not_imply_weight_updated` — boot state → `weight_updated=False`
- `test_coach_insufficient_samples_do_not_imply_weight_updated` — zero trades → `weight_updated=False`
- `test_coach_weight_updated_false_when_recompute_not_run` — test-env default state → `weight_updated=False`
- `test_coach_recompute_event_sets_weight_updated_true` — simulated `updated_at` → `weight_updated=True`
- `test_coach_thesis_db_readiness_does_not_imply_thesis_resolved` — `THESIS_TRACKER_DB_READY=True` → `thesis_resolved=False`
- `test_coach_thesis_resolved_false_during_ordinary_analysis` — full_analysis context → `thesis_resolved=False`
- `test_coach_active_thesis_does_not_imply_thesis_resolved` — active THESIS_BY_INST entry → `thesis_resolved=False`
- `test_coach_learning_influence_matches_lsi_delta` — `learning_influence` equals active-direction delta from LSI
- `test_coach_learning_influence_not_a_nested_object` — `learning_influence` is scalar float, not dict
- `test_coach_rule_engine_eligibility_not_recalculated` — repeated calls → consistent result + LEARNING_ELIGIBILITY cache unchanged
- `test_coach_repeated_reads_do_not_write` — 3 calls → LEARNING_ANALYTICS, STRATEGY_WEIGHTS, THESIS_BY_INST, LEARNING_ELIGIBILITY all unchanged

**Manager (7):**
- `test_manager_active_trade_scoped_to_requested_instrument` — MNQ trade injected; MGC request → `None`
- `test_manager_active_trade_no_fallback_to_other_instrument` — MGC trade injected; MNQ request → `None`
- `test_manager_active_trade_is_copy_not_live_reference` — mutating returned dict does not alter `ACTIVE_TRADES_BY_INST`
- `test_manager_managed_trade_is_copy_not_live_reference` — mutating returned dict does not alter `MANAGED_TRADES_BY_KEY`
- `test_manager_auto_trade_values_are_bools` — every value in auto_trade_enabled is a Python `bool`
- `test_manager_auto_trade_covers_all_assets` — auto_trade_enabled covers all registered ASSETS
- `test_manager_training_gate_meaning_is_arm_status_not_gate_verdict` — `training_gate.enabled` matches `training_mode_enabled()` (env-var), not a per-call gate verdict
- `test_manager_builder_does_not_change_active_trade_count` — active trade count unchanged after builder calls
- `test_manager_builder_does_not_trigger_execution` — `_TRADERSPOST_LAST` unchanged after builder calls
