---
name: Gate Effectiveness Audit (Phase 8C)
description: Measurement-only system recording every gate decision (ALLOWED+BLOCKED) with counterfactual outcome tracking. NEVER changes gate, execution, or risk.
---

# Gate Effectiveness Audit — Phase 8C

## Core principle
MEASURE FIRST, CHANGE SECOND. This system ONLY observes. It never modifies gate logic, Edge Score, sizing, arm state, or execution.

## Key files
- `gate_effectiveness.py` — full module: `record_gate_decision()`, `_gate_audit_watcher_cycle()`, analytics functions, `check_gate_audit_db_ready()`
- `gate_baseline_2026_08_11.json` — immutable config snapshot (thresholds, flags, weights, R:R at time of deployment)
- `db_gate_effectiveness_schema.sql` — DDL for `gate_audit_log` table; applied to dev; still needs prod apply (Publish)
- `tests/test_gate_effectiveness.py` — 34 tests, all pass

## Integration points in app.py
1. `GATE_AUDIT_DB_READY = False` flag near other DB flags
2. `_check_gate_audit_db_ready()` function (boot probe, same pattern as GRE/EdgeLedger)
3. Called at startup after `_check_edge_ledger_db_ready()`
4. full_analysis hook: just before `return result`, after DC observer block
5. Watcher start: `threading.Timer(30, _ge_start.schedule_watcher).start()` at startup, gated on `GATE_AUDIT_DB_READY`
6. Flask routes: `/gate-effectiveness`, `/gate-effectiveness/missed-winners`, `/gate-effectiveness/saved-losses`
7. Proxy whitelist: `artifacts/api-server/src/routes/flask-proxy.ts` — 3 routes added

## gate_audit_log table
- `audit_id` (PRIMARY KEY): deterministic dedup key — 1-hour bucket for BLOCKED, 10-minute bucket for ALLOWED
- Records both verdicts with full gate component states (PASS/FAIL/UNAVAILABLE), all blockers, primary blocker, geometry, market context
- `outcome_status`: PENDING → COMPLETED/EXPIRED/NO_GEOMETRY (watcher resolves BLOCKED; ALLOWED linked to strategy_trades)
- `tp1_hit` without `tp2_hit` → closes at 1.0R (TP1 achieved, runner still open but observation closed conservatively)

## Evidence levels
ANECDOTAL (<10) → EARLY (≥10) → MODERATE (≥30) → STRONGER_EVIDENCE (≥100) per rule/bucket
System returns NOT_ENOUGH_DATA until MODERATE is reached. No recommendation is valid below MODERATE.

## Boot log confirmation
```
GateEffectiveness: gate_audit_log ready (baseline=GATE_BASELINE_2026_08_11)
GateEffectiveness: counterfactual watcher scheduled (30s delay)
```

## Prod status
- Dev DB: table + 6 indexes created
- Prod DB: NOT YET APPLIED (needs Publish → schema-diff OR direct DB tool)
- Without prod apply: GATE_AUDIT_DB_READY stays False on live bot; no data accumulates

**Why:** We want to confirm observational wiring is solid in dev before applying to production and starting the measurement clock on real trade decisions.
