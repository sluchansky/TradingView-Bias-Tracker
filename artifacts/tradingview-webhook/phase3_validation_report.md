# Phase 3 Production Validation — Canonical Decision Contract
**Date:** 2026-08-11  
**Validator:** Replit Agent  
**Environment:** Development (dev DB) + pre-production code review  
**Shadow mode throughout — no live trading behaviour changed**

---

## §1 — Boot Confirmation

**PASS**

```
INFO:__main__:DecisionContract: DB tables (decision_records/decision_transitions) ready
INFO:decision_contract.DC:DecisionContract: DB tables ready
INFO:decision_contract.DC:DecisionContract: restored 1 active records
INFO:__main__:DecisionContract: Phase 3 shadow registry initialised (4 instruments)
```

Both expected log lines present. `DC_DB_READY = True` (app.py module global) and `DecisionRegistry.DC_DB_READY = True` (class var, set by `boot()`) confirmed after bug fix §10-A.

---

## §2 — Shadow Mode Confirmation

**PASS**

- `shadow_mode=True` passed at `DecisionRegistry(...)` boot call in app.py (~line 80885)
- `/decision-state` response: `"shadow_mode": true` in every response
- `DECISION_CONTRACT_SHADOW_MODE` env key defined at line 47 of `decision_contract.py`
- All 5 integration hooks in app.py wrapped in `try/except` — fail-open
- No canonical record path touches broker send, auto-fire, risk reservation, or edge scoring

---

## §3 — Parity Checks

**ALL PASS**

| Check | Result |
|---|---|
| PARITY (registry/resolver) | ✅ PARITY OK — byte-identical to baseline |
| SCALP GOLDEN | ✅ SCALP GOLDEN OK — byte-identical to baseline |
| DUAL-SIM SMOKE (MODE=SCALP) | ✅ OK — fidelity + money-path isolation + dashboard `node --check` |
| BREAKOUT MODE SMOKE | ✅ OK — + dashboard `node --check` |

---

## §4 — Live Canonical State (Dev)

**PASS**

`GET /decision-state` returns:
```json
{
  "ok": true,
  "shadow_mode": true,
  "ready": true,
  "count": 4,
  "summary": { "WAIT": 4 },
  "records": [
    { "instrument": "MGC", "state": "WAIT", "reason_code": "NO_STRUCTURE", "parity_agree": true },
    { "instrument": "MNQ", "state": "WAIT", "reason_code": "NO_SETUP",    "parity_agree": true },
    { "instrument": "MES", "state": "WAIT", "reason_code": "VWAP_CONFLICT","parity_agree": true },
    { "instrument": "MYM", "state": "WAIT", "reason_code": "NO_STRUCTURE", "parity_agree": true }
  ]
}
```
All 4 instruments tracked. All WAIT (market closed overnight). `parity_agree=true` on all.

---

## §5 — Transition History (Dev DB)

**PASS**

```
decision_records: 4 rows (MGC, MNQ, MES, MYM)
decision_transitions: 3 rows (initial OBSERVING→WAIT boot transitions)
parity_mismatches: 0
```

Records persisting correctly to `decision_records` (ON CONFLICT DO UPDATE) and transitions to `decision_transitions`. Daemon-thread persistence operational after bug fix §10-A.

---

## §6 — ORB State Machine Mapping

**CODE-MAPPED — NOT YET OBSERVED LIVE**

- `ORB_STATE_MAP` in `decision_contract.py` covers all ORB states → canonical states
- `observe_orb_state()` wired in `_orb_bar_close()` in app.py (~line 28997)
- `OrbEngine: boot — DB ready, today's state restored` in boot log
- No 09:30 ET session has occurred since last restart → no live ORB observations yet
- Expected first live observation: next trading day at 09:30 ET

---

## §7 — Auto-Fire Code Path Mapping

**CODE-MAPPED — NOT YET OBSERVED LIVE (execution hooks missing)**

- `observe_entry_requested()` method exists in `DecisionRegistry` (line 954)
- `observe_manual_requested()` method exists in `DecisionRegistry` (line 970)
- **GAP:** Neither is called in app.py — `_maybe_auto_execute()` and the manual ENTER path do not advance canonical state beyond EXECUTABLE → ENTRY_REQUESTED
- `full_analysis()` seam captures arm state snapshot, so EXECUTABLE state IS observed
- Execution transitions (ENTRY_REQUESTED, ORDER_ACCEPTED, POSITION_ACTIVE) will NOT appear in `decision_transitions` until hooks are wired (Phase 4 item — see follow-up tasks)

---

## §8 — Ghost Research Snapshot Enrichment

**GAP — NOT WIRED**

- `enrich_ghost_snapshot()` function fully implemented in `decision_contract.py` (line 516–565)
- Adds: `canonical_decision_state`, `canonical_reason_code`, `canonical_decision_id`, `live_verdict`, `edge_score`, `confidence`, `fvg_state`, `zone_state`, `qualification_state`, `risk_status`, `execution_mode`, `armed`, `safety_lock`, `prop_status`, `orb_canonical_state`, `parity_agree`, `parity_diff_reason`, `dc_version`
- **`_ghost_observe_setup()` in app.py does NOT call `enrich_ghost_snapshot()`**
- Ghost observation snapshots do NOT include canonical DC fields as of this report
- Deferred to Phase 4 (see follow-up tasks)

---

## §9 — Parity Mismatch Monitor

**PASS — Zero mismatches observed**

```
total_records: 4
parity_mismatches: 0
```

Legacy verdict and canonical state agree on all 4 instruments. Legacy `WAIT` → canonical `WAIT` mapping confirmed for all reason codes observed (NO_STRUCTURE, NO_SETUP, VWAP_CONFLICT). Mismatch monitor columns (`parity_agree`, `parity_diff_reason`) in `decision_records` are populated on every persist.

Note: any mismatch (e.g., a READY setup where canonical maps to BLOCKED_*) would be logged via `_re_event("DC_PARITY_MISMATCH", ...)` and persisted with `parity_agree=false`. No such case encountered during market-closed period.

---

## §10 — Bugs Found and Fixed During Validation

### §10-A (Critical) — Persistence silently skipped due to missing `boot()` call

**Root cause:** `DecisionRegistry` has two separate DB-ready flags:
- `DC_DB_READY` in app.py (module global) — set by `_check_dc_db_ready()` at boot
- `DecisionRegistry.DC_DB_READY` (class variable on the registry class) — only set by `registry.boot()`

The boot block in app.py created the registry with `_DCReg(...)` but never called `_DECISION_REGISTRY.boot()`. The class variable stayed `False`. Every daemon-thread persistence call checked `if DecisionRegistry.DC_DB_READY:` → silently skipped. Result: 0 rows in DB despite active observations.

**Fix:** Added `_DECISION_REGISTRY.boot()` immediately after the registry constructor call.

**Verification:** After fix, boot log shows `DecisionContract: restored 1 active records` and DB has 4 live rows.

### §10-B — `/decision-state` route crashed with `'str' object has no attribute 'get'`

**Root cause:** `registry.get_all_states()` returns `Dict[str, Optional[Dict]]` (keyed by instrument). Route code iterated it directly (`for s in all_states`) — iterating a dict gives string keys, so `s.get("decision_id")` crashed.

**Fix:** Converted to list of values: `records_list = [v for v in states_by_inst.values() if v is not None]`

### §10-C — Route called non-existent `get_transitions()` method

**Root cause:** Route used `registry.get_transitions(decision_id=...)` which doesn't exist. Correct method is `registry.get_history(inst, limit=N)`.

**Fix:** Updated all `get_transitions()` calls to `get_history()`.

---

## §11 — Final Report Summary

| Check | Status | Notes |
|---|---|---|
| Boot log confirms DC tables ready + registry initialised | ✅ PASS | After §10-A fix |
| `DC_DB_READY = True` (class var) via `boot()` | ✅ PASS | After §10-A fix |
| Shadow mode enforced (`shadow_mode=True`) | ✅ PASS | No money path touched |
| PARITY / SCALP GOLDEN / DUAL-SIM / BREAKOUT | ✅ ALL PASS | Byte-identical to baseline |
| 166 Decision Contract tests | ✅ PASS | |
| 579 DC + GRE + canonical + ORB + FVG tests | ✅ PASS | |
| `/decision-state` returns 4-instrument snapshot | ✅ PASS | After §10-B / §10-C fixes |
| DB persistence: 4 records + 3 transitions live | ✅ PASS | After §10-A fix |
| Parity mismatches: 0 | ✅ PASS | |
| `full_analysis` and `_orb_bar_close` observe hooks wired | ✅ PASS | Fail-open in app.py |
| ORB live observation (09:30 ET session) | ⏳ PENDING | Market closed; code-mapped |
| Auto-fire ENTRY_REQUESTED hook wired | ⚠️ GAP | Method exists; not called |
| Manual ENTER hook wired | ⚠️ GAP | Method exists; not called |
| Ghost snapshot enrichment (`canonical_decision_state`) | ⚠️ GAP | `enrich_ghost_snapshot()` not called from `_ghost_observe_setup()` |
| Production tables applied | ⏳ PENDING | Blocked on publish |

### Standing invariants confirmed

1. Every DC hook in app.py wrapped in `try/except` — fails open, never blocks `full_analysis()` or broker send
2. No DDL in app.py — tables exist via DB tool only
3. `/decision-state` has no Flask-level `@_owner_required` (Express auth sufficient; same pattern as ghost-research routes)
4. `shadow_mode=True` hardcoded at boot call — promotion requires deliberate env change

### Production readiness

The Phase 3 decision contract is **shadow-ready in dev**. Production activation requires:
1. Publish the app (schema diff applies `decision_records` + `decision_transitions`)
2. Confirm production boot log matches §1

Three Phase 4 items deferred as follow-up tasks: ghost enrichment wiring, execution-path hooks, and production publish confirmation.
