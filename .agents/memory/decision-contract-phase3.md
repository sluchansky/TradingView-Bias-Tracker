---
name: Canonical Decision Contract Phase 3
description: Shadow-only typed decision state machine; wiring points in app.py; 166 tests; DB tables; route.
---

## Rule
Phase 3 introduces a canonical, typed, per-instrument decision state machine (`decision_contract.py`) that runs in **shadow mode only** — it records and audits the trading lifecycle but never gates, sizes, or sends orders.

## Key files
- `decision_contract.py` — DecisionState enum, ReasonCode, LEGAL_TRANSITIONS, ORB_STATE_MAP, DecisionRecord, DecisionTransition, DecisionRegistry
- `test_decision_contract.py` — 166 tests covering all §20 spec scenarios
- `app.py` — 5 integration points (see wiring below)
- `artifacts/api-server/src/routes/flask-proxy.ts` — `/decision-state` in BOT1_ROUTES

## Wiring in app.py (all fail-open, shadow only)
1. **Global flag**: `DC_DB_READY = False` — next to `GRE_DB_READY`
2. **Probe function**: `_check_dc_db_ready()` — placed after `_check_gre_db_ready()`, probes `decision_records` + `decision_transitions` tables
3. **Boot block** (after CanonicalMarketState block): calls `_check_dc_db_ready()`, then imports `DecisionRegistry` and stores as `globals()["_DECISION_REGISTRY"]` with `shadow_mode=True`
4. **`full_analysis()` seam** (before `return result`): calls `_DECISION_REGISTRY.observe_full_analysis(active_ticker, result, dict(_ARM_STATE))` — fail-open
5. **`_orb_bar_close()` seam** (after GRE call): calls `_DECISION_REGISTRY.observe_orb_state(inst, _orb_status)` via `_ORB_ENGINE.get_instrument_status(inst)` — fail-open

## Flask route
`GET /decision-state` — owner-only (`@_owner_required`), placed before `/research-events`.
Query params: `instrument=`, `decision_id=`, `transitions=1`, `mismatches=1`.

## DB tables (created via DB tool / publish schema-diff — NO DDL in app.py)
- `decision_records` — current state per decision_id (parity fields included)
- `decision_transitions` — full audit history

## Design decisions
- SHADOW MODE ONLY: `shadow_mode=True`; canonical record never gates broker transmission
- EARLY → EXECUTABLE in LEGAL_TRANSITIONS as legacy compat path (not promoted, not changed)
- Shadow compression: READY → EXECUTABLE is legal shortcut in shadow mode (QUALIFIED/RISK_PENDING/RISK_APPROVED not independently observable without deep gateway instrumentation)
- BLOCKED_RISK may not jump directly to EXECUTABLE; it must recover through a non-executable requalification/reset state first.
- SETUP_FORMING → QUALIFIED added (ORB TOUCH/CLOSE_OUTSIDE confirmation modes skip EARLY)
- EARLY → QUALIFIED added (ORB retest-holds path)
- `_owner_required` is defined AFTER some route decorators in app.py — confirmed pre-existing collection errors in test_brain_contract/test_learning_engine etc; NOT caused by Phase 3

**Why:** Creates an auditable, typed record of every decision lifecycle step so Phase 4+ can promote it to a live gate without rewriting the logic from scratch.
**How to apply:** When adding new states or transitions, update LEGAL_TRANSITIONS in decision_contract.py AND add a test in TestLegalTransitions. Preserve the BLOCKED_RISK requalification boundary. Never add DDL to app.py — use executeSql or publish schema-diff.
