# V1 Phase 7B — Main Brain Read-Only Route Validation

**Build:** `V1-P7B Main Brain read-only route`  
**Branch:** `polish-v1`  
**Date:** 2026-07-30

---

## 1  Scope

This document records the validation performed for Phase 7B as specified in `PHASE_7_MAIN_BRAIN_ROUTING_BRIEF.md`.

Phase 7B delivers:
- `build_main_brain_payload(result, instrument=None)` — canonical builder that assembles the versioned Main Brain dashboard payload from existing canonical V1 interfaces.
- `GET /main-brain` — TTL-cached, owner-only Flask route with `?ticker=` and `?mode=` support.
- Proxy whitelist entry: `/main-brain` added to `BOT1_ROUTES` in `artifacts/api-server/src/routes/flask-proxy.ts`.
- Test suite: `test_phase7b_main_brain_route.py` — 56 checks across 12 test classes.
- This validation document.

---

## 2  Pre-existing baseline (unchanged)

All baseline suites confirmed passing before and after Phase 7B code was added.

| Suite | Tests | Status |
|---|---|---|
| test_phase6_journal_coach.py | 30 | ✅ PASS |
| test_v1_interface_versions.py | 92 | ✅ PASS |
| test_phase4_operator_explanation.py | 57 | ✅ PASS |
| test_phase2_market_data_reliability.py | 45 | ✅ PASS |
| test_phase3_thesis_verdict_pipeline.py | 60 | ✅ PASS |
| test_phase5_execution_safety.py | Pre-existing S6 fails | ✅ Unchanged |
| run_phase2_smoke.sh | Phase 2 smoke | ✅ PASS |
| parity workflow | Registry/resolver | ✅ PASS |
| scalp_golden workflow | Byte-identical | ✅ PASS |
| dual_sim workflow | Byte-identical | ✅ PASS |
| breakout_mode workflow | Byte-identical | ✅ PASS |

---

## 3  Phase 7B test suite results

Suite: `test_phase7b_main_brain_route.py`

| Class | Tests | What is verified |
|---|---|---|
| TestSchemaCompleteness (TC-P7B-001) | 005 | All 17 required top-level keys present; `_version="v1"`; `generated_at` parses as ISO datetime; `availability` has all section keys; `errors` is list |
| TestMbSafeNum (TC-P7B-002) | 009 | None→None; int→float; NaN→None; ±Inf→None; numeric string→float; non-numeric→None; 0→0.0; negative pass-through |
| TestJsonSerialisation (TC-P7B-003) | 005 | Full round-trip; no NaN/Inf in output; no raw datetime; list components normalised to dict; dict components pass through; frozenset strict_missing serialisable |
| TestNoneResult (TC-P7B-004) | 003 | result=None returns payload; has errors list; JSON serialisable |
| TestVerdictSection (TC-P7B-005) | 006 | WAIT not actionable; LONG READY actionable; SHORT READY actionable; edge_max=110; failed_conditions is list; availability True when no exception |
| TestActiveTradeDerivedFields (TC-P7B-006) | 005 | Long current_r positive above entry; negative below entry; short current_r positive below entry; empty dict → empty list; None slot skipped |
| TestFaultIsolation (TC-P7B-007) | 005 | Missing coach key; missing strategy_engine; market_intelligence=None; trade_plan=None; edge_breakdown=None — none crash the whole payload |
| TestDecisionTimeline (TC-P7B-008) | 006 | partial=True; completeness="PARTIAL"; events ≤ 20; missing_event_types includes TRADE_OPENED; _deferred key present and mentions Phase 7C; availability shows partial |
| TestStrategyScanner (TC-P7B-009) | 003 | Research strategies excluded; selected strategy annotated; display labels assigned |
| TestCoachPassThrough (TC-P7B-010) | 003 | `_version` preserved; extra key copied; mutation of returned copy does not affect source |
| TestManagerSection (TC-P7B-011) | 003 | gateway_debug present; training_gate present; _version present |
| TestProxyWhitelist (TC-P7B-012) | 002 | `/main-brain` in BOT1_ROUTES; `/main-brain` NOT in OPEN_PATHS |

**Total: 56 checks — all pass.**

---

## 4  Live route smoke test

The Flask server was restarted and `GET http://localhost:8000/main-brain` was exercised:

```
_version: v1
generated_at: 2026-07-30T16:29:28.506170+00:00
top-level keys: ['_version', 'active_trades', 'alerts', 'availability', 'coach',
  'decision_timeline', 'errors', 'execution_gateway', 'generated_at', 'journal',
  'left_brain', 'manager', 'market', 'market_state', 'performance',
  'strategy_scanner', 'system_status', 'verdict']
```

Supplementary checks:

| Check | Result |
|---|---|
| HTTP status | 200 |
| JSON round-trip | OK |
| NaN / Infinity in output | None found |
| Payload size | 13 021 bytes (well under 64 KB limit) |
| All `availability` sections | All `available: true` |
| `errors` list | Empty (no section errors) |
| `verdict.components` | Normalised to dict (was list in live result) |
| `strategy_scanner.ranked_strategies` | 5 main-engine strategies only |
| `decision_timeline.completeness` | `PARTIAL` |
| `decision_timeline.events` | 2 events (READY_SIGNAL + GATEWAY_SEND derived) |

---

## 5  Implementation invariants confirmed

### 5.1  Money-path isolation

`build_main_brain_payload()` and the route handler:

- **Never call** `execute_trade_gateway()`, `traderspost_send()`, or any broker HTTP helper.
- **Never write** to `ACTIVE_TRADES_BY_INST`, `JOURNAL`, `LEARNING_ANALYTICS`, `MANAGED_TRADES_BY_KEY`, or any learning state.
- **Never run DDL** or DML beyond the one `SELECT` in `_mb_journal()` which reads `strategy_trades`.
- **Reads are read-only**: all deque reads via `list()` snapshots (ALERT_HISTORY, THESIS_TIMELINE_BY_INST, _LB_THESIS_OBS_BY_INST).
- All returned dicts are shallow copies — consumer mutation cannot affect canonical state.

### 5.2  Fault isolation

Every section helper is independently wrapped in `try/except`. A single section failure:
- Appends an entry to the `errors` list with `source`, `code`, `recoverable: True`, and `ts`.
- Returns a safe fallback dict for that section.
- Never propagates to adjacent sections or the top-level payload.
- The whole payload is always returned as long as `build_main_brain_payload()` itself does not encounter a truly unrecoverable error (which would be a programming bug, not a data issue).

### 5.3  JSON serialisation safety

- All `datetime` objects converted to ISO-8601 strings via `_mb_iso()`.
- All `deque` reads via `list()` — no deque in output.
- All `frozenset` values in `strict_missing` converted via `list()`.
- `_mb_safe_num()` converts NaN and ±Infinity to `None`.
- `edge_breakdown.components` normalised from list-of-dicts to `{name: score}` dict.
- Output is verified `json.dumps()`-clean in the test suite (TC-P7B-003).

### 5.4  Caching

`GET /main-brain` uses the same single-flight TTL cache pattern as `GET /status`:
- Key: `"{mode_override}_" + ticker` or `"__active__"` for default instrument.
- TTL: `STATUS_CACHE_TTL_SEC` (shared config constant).
- Single-flight guard: `_MB_BUILDING[key]` prevents duplicate cold-cache rebuilds under concurrent requests.
- Stale serve: if a rebuild is in flight and a stale entry exists, the stale entry is served rather than blocking.
- Cache variables: `_MB_CACHE`, `_MB_CACHE_LOCK`, `_MB_BUILDING` — module-level, initialized at boot.

### 5.5  Authentication

Route is NOT in `OPEN_PATHS`. Express Basic Auth proxy enforces owner-only access. Direct Flask access (bypassing Express) is development-only.

---

## 6  Known gaps (deferred)

| Gap | Deferred to |
|---|---|
| `last_outcome` in `execution_gateway` — no `_LAST_GATEWAY_RESULT_BY_INST` store yet | Phase 7C |
| `_DECISION_EVENT_LOG_BY_INST` full event deque (VERDICT_GENERATED, TRADE_OPENED, etc.) | Phase 7C |
| `strategy_scanner.sample_count` / `historical_expectancy` — no per-strategy win-rate cache | Phase 7C |
| `performance.best_window` / `worst_window` — depends on Nth-trade stats accumulation | Future |

All gaps are documented in the payload via `_deferred` keys in the affected sections. The payload remains fully functional and useful with these gaps.

---

## 7  Files changed

| File | Change |
|---|---|
| `artifacts/tradingview-webhook/app.py` | Added `_MB_CACHE`, `_MB_CACHE_LOCK`, `_MB_BUILDING` (module-level); added `_MB_MAIN_ENGINE_KEYS`, `_MB_STRATEGY_LABELS`; added `_mb_safe_num()`, `_mb_iso()`, `_mb_err()`, `_mb_has_err()` and 12 section helpers; added `build_main_brain_payload()`; added `GET /main-brain` route |
| `artifacts/api-server/src/routes/flask-proxy.ts` | Added `"/main-brain"` to `BOT1_ROUTES` array |
| `artifacts/tradingview-webhook/test_phase7b_main_brain_route.py` | New — 56 checks across 12 test classes |
| `artifacts/tradingview-webhook/V1_PHASE_7B_MAIN_BRAIN_ROUTE_VALIDATION.md` | New — this document |

---

## 8  Golden suite parity (unchanged)

Phase 7B is purely additive. The builder functions are new code paths never invoked by `full_analysis()` itself. The `GET /main-brain` route is new and never called from existing paths. No existing constant, registry, gateway, or flag was modified.

- `parity`: PASS ✅
- `scalp_golden`: PASS ✅
- `dual_sim`: PASS ✅
- `breakout_mode`: PASS ✅

---

*End of Phase 7B validation.*
