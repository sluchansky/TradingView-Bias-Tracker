---
name: Decision Contract Phase 3 Closure
description: DC wiring to GRE, execution hooks, DB migration, new legal transitions, and test patterns
---

## What was done

Phase 3 of the Canonical Decision Contract wired 6 observation hooks into app.py and enriched
the Ghost Research Engine with DC state at opportunity-freeze time.

## New legal transitions added (and why)

Scalp/non-ORB live fires go READY → ENTRY_REQUESTED directly (no QUALIFIED/EXECUTABLE step)
because scalp full_analysis compresses those gates. Without these transitions, every scalp fire
would produce an ILLEGAL TRANSITION log and DC would never record it:

- `READY → ENTRY_REQUESTED` — scalp direct auto-fire
- `EARLY → ENTRY_REQUESTED` — EARLY half-size scalp auto-fire
- `READY → MANUAL_REQUESTED` — operator ENTER on a live READY setup
- `EARLY → MANUAL_REQUESTED` — operator ENTER on an EARLY setup
- `MANUAL_REQUESTED → ORDER_ACCEPTED` — manual trade broker 2xx (bypasses ENTRY_REQUESTED)
- `MANUAL_REQUESTED → ORDER_REJECTED` — manual trade broker 4xx

**Why:** These are confirmed production behaviour paths, not test conveniences. The ORB path
(OBSERVING → SETUP_FORMING → ... → EXECUTABLE → ENTRY_REQUESTED) is intact and unchanged.

## DB migration

`ghost_opportunities` got 13 new nullable DC columns:
dc_decision_id, dc_state, dc_reason_code, dc_verdict, dc_edge_score, dc_confidence,
dc_qualified, dc_risk_status, dc_execution_mode, dc_execution_enabled, dc_armed,
dc_parity_agree, dc_version

All NULL when DC unavailable (fail-open). Columns are populated by `enrich_ghost_snapshot()`
called inside GRE._on_breakout_detected after building the base snapshot.

## GRE dc_registry_fn wiring

`GhostResearchEngine.__init__` now accepts `dc_registry_fn: Optional[Callable] = None`.
app.py passes `lambda: globals().get("_DECISION_REGISTRY")` at GRE boot — lazy getter so
GRE initialises before DC registry without circular dependency.

## app.py hook locations

| Hook | Location | Trigger |
|---|---|---|
| observe_entry_requested | _maybe_auto_execute, before execute_trade_gateway | auto scalp fire |
| observe_manual_requested | traderspost_order, before execute_trade_gateway | dashboard ENTER |
| observe_manual_requested | manual_desk_order, before execute_trade_gateway | manual desk |
| observe_order_accepted | _send_broker_order, 2xx branch after _record_broker_send | broker accepted |
| observe_order_rejected | _send_broker_order, 4xx branch after _record_broker_send | broker rejected |
| observe_completed | _close_managed_trade, before SWING persistence block | trade closes |

All hooks follow the canonical pattern:
```python
if DC_DB_READY and "_DECISION_REGISTRY" in globals():
    try:
        globals()["_DECISION_REGISTRY"].method(...)
    except Exception as _exc:
        logger.debug(...)
```
_send_broker_order hooks omit DC_DB_READY check (the 2xx/4xx split is already inside a try).

## observe_order_accepted / observe_order_rejected

Two new methods added to `DecisionRegistry`. Both use `_simple_transition` — fail-open,
no new state persistence beyond what _simple_transition already does.

## get_record(inst) method

Added to `DecisionRegistry` — returns the `DecisionRecord` for an instrument or None.
Used by GRE to retrieve the record before calling `enrich_ghost_snapshot`.

## Test fixture pattern for observe_full_analysis

`arm_state` must include `execution_enabled=True` and `configured_mode="traderspost"`:
- `armed=False` → READY state (not armed, execution available)
- `armed=True`  → EXECUTABLE state (armed and enabled)
- `execution_enabled=False` → BLOCKED_EXECUTION_MODE (execution disabled)

DecisionRecord dataclass fields: `decision_id, opportunity_id, instrument, strategy,
strategy_version, direction, state, previous_state, state_changed_at, reason_code,
reason_text, verdict, edge_score, confidence, market_context_ref, canonical_state_ts,
entry, stop, tp1, tp2, quantity, risk_status, risk_amount, risk_r, risk_reservation_id,
execution_mode, execution_enabled, arm_required, armed, safety_lock, prop_status,
source_module, transition_history, created_at, updated_at, expires_at, legacy_verdict,
canonical_state, parity_agree, parity_diff_reason`

Note: NO `trading_date` field on DecisionRecord.

## Phase 3.1 — OBSERVING → WAIT transition gap (added later)

`OBSERVING → WAIT` was absent from LEGAL_TRANSITIONS, causing repeated ILLEGAL TRANSITION
warnings whenever full_analysis returned WAIT after ORB reset DC to OBSERVING (SESSION_CLOSED).

Root cause: `map_full_analysis_to_canonical` calls `_map_strict_reason` which returns
`(DS.WAIT, reason_code)` for verdict=="WAIT". Legal transition was not defined.

Fix: added `(DS.OBSERVING, DS.WAIT)` to the decay/reset block in `_build_legal_transitions`.
Reason code (VWAP_CONFLICT, NO_STRUCTURE, etc.) is preserved — not replaced with UNKNOWN.

Post-fix production confirmation: 4 new OBSERVING→WAIT rows in decision_transitions within
seconds of restart, all with VWAP_CONFLICT and full reason text. Zero ILLEGAL TRANSITION
warnings in boot log.

34 new Phase 3.1 tests added: `tests/test_dc_phase3_1_transition_cleanup.py`.

## Regression state after this work

- 45 new DC Phase 3 tests: 45/45 ✅
- 297 existing GRE/ORB/canonical tests: 297/297 ✅  
- 274 profitability/8B tests: 274/274 ✅
- All 4 smokes: parity, scalp_golden, dual_sim, breakout_mode ✅
