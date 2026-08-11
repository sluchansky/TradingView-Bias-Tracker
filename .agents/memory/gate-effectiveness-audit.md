---
name: Gate Effectiveness Audit (Phase 8C)
description: Measurement-only system recording every gate decision (ALLOWED+BLOCKED) with counterfactual outcome tracking. NEVER changes gate, execution, or risk.
---

# Gate Effectiveness Audit — Phase 8C

## Core principle
MEASURE FIRST, CHANGE SECOND. This system ONLY observes. It never modifies gate logic, Edge Score, sizing, arm state, or execution.

## Key files
- `gate_effectiveness.py` — full module: `record_gate_decision()`, `_extract()`, `_scalar_str()`, `_record()`, `validate_wiring()`, analytics functions, `check_gate_audit_db_ready()`
- `gate_baseline_2026_08_11.json` — immutable config snapshot (thresholds, flags, weights, R:R at time of deployment)
- `db_gate_effectiveness_schema.sql` — DDL for `gate_audit_log` table; applied to dev; still needs prod apply (Publish)
- `tests/test_gate_effectiveness.py` — 34 tests, all pass
- `tests/test_gate_effectiveness_wiring.py` — 25 wiring regression tests, all pass

## Integration points in app.py
1. `GATE_AUDIT_DB_READY = False` flag near other DB flags
2. `_check_gate_audit_db_ready()` function (boot probe, same pattern as GRE/EdgeLedger)
3. Called at startup after `_check_edge_ledger_db_ready()`
4. full_analysis hook: just before `return result`, after DC observer block
5. Watcher start: `threading.Timer(30, _ge_start.schedule_watcher).start()` at startup, gated on `GATE_AUDIT_DB_READY`
6. Flask routes: `/gate-effectiveness`, `/gate-effectiveness/validate-wiring` (POST), `/gate-effectiveness/missed-winners`, `/gate-effectiveness/saved-losses`
7. Proxy whitelist: `artifacts/api-server/src/routes/flask-proxy.ts` — 4 routes added

## gate_audit_log table
- `audit_id` (PRIMARY KEY): deterministic dedup key — 1-hour bucket for BLOCKED, 10-minute bucket for ALLOWED
- Records both verdicts with full gate component states (PASS/FAIL/UNAVAILABLE), all blockers, primary blocker, geometry, market context
- `outcome_status`: PENDING → COMPLETED/EXPIRED/NO_GEOMETRY (watcher resolves BLOCKED; ALLOWED linked to strategy_trades)
- `tp1_hit` without `tp2_hit` → closes at 1.0R (TP1 achieved, runner still open but observation closed conservatively)

## Critical bugs fixed (wiring session)

### Bug 1: direction always "Unknown" → all BLOCKED records dropped
- Root cause: `strict_direction` is None on WAIT, verdict is bare "WAIT" (no direction prefix)
- Fix: `_extract()` now parses direction from `strict_reason` as primary fallback (e.g. "Short WAIT — ..." → "Short")
- Secondary fallback: `directions` dict keys if exactly one side present
- Guard: `direction in (None, "Unknown") and gate_verdict == "BLOCKED"` → skip correctly when truly no candidate

### Bug 2: `session_state` returned as dict → psycopg2 `can't adapt type 'dict'`
- Root cause: `result.get("session_state")` returns `{'preferred': False, 'bonus': 0, 'window': '...'}` dict, not a string
- Other fields at risk: `trend_alignment`, `cvd_direction` may also be dicts in some states
- Fix: added `_scalar_str(val)` helper that collapses dicts to their most readable string field (window/label/status/name/regime), applied to `session`, `trend_alignment`, `cvd_direction` in `_extract()`
- **Why:** full_analysis result fields evolve over time and several embed structured objects rather than scalars; any new field added to `_extract()` that comes from `result.get()` must go through `_scalar_str()` or a similar guard

## GATE_AUDIT_TRACE logging
- Fires at INFO level on every successful INSERT: `GATE_AUDIT_TRACE instrument=X direction=Y mode=Z decision=BLOCKED|ALLOWED recorder_called=true audit_id=... edge=... blocker=...`
- Early returns log at DEBUG level (not visible in prod logs unless debug logging enabled)
- `_LAST_RECORDED_AT` global updated after every successful INSERT; surfaced in `get_summary()` as `last_recorded_at`

## Synthetic validation
- `validate_wiring(clean_up=True)` → inserts 1 BLOCKED + 1 ALLOWED synthetic record, reads them back, deletes them
- Returns `{"verdict": "PASS|FAIL", "verified_blocked": bool, "verified_allowed": bool, ...}`
- Accessible via POST `/gate-effectiveness/validate-wiring` (owner-only)
- Dashboard has "🔌 Validate Wiring" button in the Gate Effectiveness panel header

## Evidence levels
ANECDOTAL (<10) → EARLY (≥10) → MODERATE (≥30) → STRONGER_EVIDENCE (≥100) per rule/bucket
System returns NOT_ENOUGH_DATA until MODERATE is reached. No recommendation is valid below MODERATE.

## Boot log confirmation (working state)
```
GateEffectiveness: gate_audit_log ready (baseline=GATE_BASELINE_2026_08_11)
GateEffectiveness: counterfactual watcher scheduled (30s delay)
...
GATE_AUDIT_TRACE instrument=MNQ direction=Long mode=SCALP decision=BLOCKED recorder_called=true ...
```

## Prod status
- Dev DB: table + 6 indexes created; recorder confirmed working (5+ live rows accumulating)
- Prod DB: NOT YET APPLIED (needs Publish → schema-diff OR direct DB tool)
- Without prod apply: GATE_AUDIT_DB_READY stays False on live bot; no data accumulates
