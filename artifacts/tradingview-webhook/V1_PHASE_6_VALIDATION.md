# V1-P6 Journal and Coach Separation — Validation Report

**Date:** 2026-07-30  
**Branch:** polish-v1  
**Anchor commit:** `19b5f26` (V1-P6 pre-implementation execution brief)  
**Validation commit:** see git log  

---

## Phase 6 Deliverables

| File | Status | Notes |
|---|---|---|
| `test_phase6_journal_coach.py` | ✅ Created | 30 new tests, all pass |
| `V1_PHASE_6_VALIDATION.md` | ✅ Created | This document |
| `test_v1_interface_versions.py` | ✅ Extended | +5 additive `thesis_last_resolved_at` tests |
| `app.py` | **UNCHANGED** | Zero production code modifications |

---

## Research Question Resolutions

### RQ-1 — open_trades close behavior
**Question:** Does `open_trades` hold `closed_at` / `result_r` after close, or is the row deleted?

**Resolution:** The row is **DELETED**. `_persist_active_trade(inst, None)` executes
`DELETE FROM open_trades WHERE inst = %s`. Closed-trade results (`closed_at`, `r_multiple`)
live entirely in `strategy_trades`, not in `open_trades`.

**Impact on test design:** V1-P6-002 split into two parts:
- Part A: asserts `open_trades` row is **absent** after close (not updated)
- Part B: asserts `strategy_trades` row has `closed_at` and `r_multiple` populated
- Extra: asserts `open_trades` schema has **no** `closed_at` or `r_multiple` columns

### RQ-2 — "unified_learning block" identity
**Question:** Is `result["unified_learning"]` a new top-level key, or is it `result["coach"]`?

**Resolution:** The unified learning block is **`result["coach"]`**, already serialized by
`_build_status_payload()` at line 44601. No new top-level `unified_learning` key was added
or is needed. V1-P6-005 tests verify `result["coach"]` presence, field contract, and
JSON serializability.

### RQ-3 — Journal Discord gate condition
**Question:** Is `send_journal_discord_embed` gated by `DISCORD_JOURNAL_WEBHOOK_URL` or
`DISCORD_LIVE_ENABLED`?

**Resolution:** Gated by **`DISCORD_JOURNAL_WEBHOOK_URL` absence only**. The function
returns immediately when the URL is not set; `DISCORD_LIVE_ENABLED` is never checked.
URL absent in test env → no HTTP call, no code change needed. V1-P6-007 verifies this.

---

## Test Results

### New test file: test_phase6_journal_coach.py

```
TOTAL: 30 checks — 30 passed, 0 failed, 0 skipped
```

| ID | Tests | Description |
|---|---|---|
| V1-P6-001 | 4 | `_record_strategy_trade` INSERT: creates row, preserves instrument/direction, populates closed_at/r_multiple, idempotent on duplicate key |
| V1-P6-002 | 3 | Open→closed transition: Part A open_trades DELETE confirmed, Part B strategy_trades fields confirmed, schema column assertions |
| V1-P6-003 | 3 | Journal failure isolation: DB exception does not propagate, execution state unchanged, no partial DB residue |
| V1-P6-004 | 4 | Journal/Coach separation: Coach reads do not write ALERT_HISTORY or ACTIVE_TRADES_BY_INST, Journal INSERT does not alter Coach fields, repeated reads are stable |
| V1-P6-005 | 6 | Coach block contract: present in full_analysis, all 6 required fields, weight_updated semantics, thesis_resolved semantics, thesis_last_resolved_at ISO-8601, JSON serializable |
| V1-P6-006 | 5 | Coach fault isolation: internal fault returns neutral stubs, full_analysis returns, verdict/edge_score unchanged, no broker call, repeated calls stable |
| V1-P6-007 | 5 | Discord journal isolation: no HTTP call when URL absent, no exception when URL absent, exactly one POST when URL configured, request exception contained, URL restored after test |

### Extended: test_v1_interface_versions.py (additive only)

```
TOTAL: 92 checks — 92 passed, 0 failed
```

Five new tests added for `thesis_last_resolved_at` (field added by merge commit `3ccc56f`):

| Test | Assertion |
|---|---|
| `test_coach_thesis_last_resolved_at_present_in_coach_dict` | Field present in every `build_coach_interface()` return |
| `test_coach_thesis_last_resolved_at_type_is_str_or_none` | Type is `str` or `None`, never another type |
| `test_coach_thesis_last_resolved_at_none_in_ordinary_analysis` | `None` when `_THESIS_LAST_RESOLVED_AT` is `None` |
| `test_coach_thesis_last_resolved_at_is_iso8601_when_set` | Valid ISO-8601 string, round-trips through `datetime.fromisoformat` |
| `test_coach_thesis_last_resolved_at_does_not_mutate_canonical_timestamp` | Reading does not alter `_THESIS_LAST_RESOLVED_AT` |

### Pre-existing suites (unchanged by Phase 6)

| Suite | Tests | Result |
|---|---|---|
| test_phase5_execution_safety.py | 137 OK / 41 FAIL | Failures **pre-existing** (confirmed by stash isolation check before any P6 code was written) |
| test_phase4_operator_explanation.py | 57/57 | ✅ |
| test_phase3_thesis_verdict_pipeline.py | 60/60 | ✅ |
| test_phase2_market_data_reliability.py | 45/45 | ✅ |
| parity workflow | — | PASS |
| scalp_golden workflow | — | PASS |
| dual_sim workflow | — | PASS |
| breakout_mode workflow | — | PASS |
| `py_compile app.py` | — | OK (app.py unchanged) |
| `git diff --check HEAD` | — | Exit 0 |

---

## Architectural invariants confirmed

1. **`app.py` not modified.** Zero production code changes.
2. **Journal and Coach are separate.** `build_coach_interface()` makes no DB writes.
   `_record_strategy_trade()` has no path into Coach state.
3. **Both are fail-open.** DB failure in Journal silently returns; internal fault in
   Coach returns neutral stubs (`_version="v1"`, `weight_updated=False`,
   `thesis_resolved=False`, `thesis_last_resolved_at=None`, `learning_influence=0.0`,
   `rule_engine_eligibility="LIVE_ELIGIBLE"`).
4. **Execution gate state is unchanged by both.** `_TRADERSPOST_LAST` is unaffected by
   Journal DB failures or Coach internal faults.
5. **Discord Journal send is URL-gated.** No HTTP call is made when
   `DISCORD_JOURNAL_WEBHOOK_URL` is absent. Request exceptions are contained.
6. **open_trades uses DELETE on close.** Closed results belong to `strategy_trades`.
   `open_trades` schema has no `closed_at` or `r_multiple` columns.
