---
name: Phase 3 Thesis Enforcement
description: Shadow-mode gate that evaluates READY setups against live market thesis without changing live behavior; enforced mode demotes verdict.
---

## Rule
`_THESIS_ENFORCEMENT_MODE` controls the gate:
- **"shadow"** (default): gate runs, records `shadow_action`/`would_change_decision`, but `action` is always `ALLOW` — verdict never touched.
- **"enforced"**: `action=="BLOCK"` → `verdict="WAIT"` (demote only, never promotes).
- **"off"**: `_compute_thesis_gate` returns `{}` immediately; key absent from `/status`.

`confidence_adjustment` in the gate result is **DISPLAY-ONLY** and is **never added to `edge_score`**.

**Why:** Shadow mode was the explicit spec requirement — run the gate for N sessions to measure false-block rate / losses-avoided before enabling real enforcement. Prematurely adding conf_adj to edge_score would double-count the thesis signal already baked into CVD/structure components.

## How to apply
- Any new "enforced" mode path must go through `_compute_thesis_gate` → check `gate["action"] == "BLOCK"` → set `verdict = "WAIT"` only (never set to READY).
- All 4 golden smoke tests must remain byte-identical — `_THESIS_ENFORCEMENT_MODE="shadow"` at boot means the gate path is exercised but output is unchanged.
- The served dashboard `<script>` must node-check clean after any JS addition to the thesis panel — the cockpit-mode \n escape bug applies here too.

## Key constants
- `THESIS_EVAL_MAX_CONF_ADJ_ALIGNED = +5`
- `THESIS_EVAL_MAX_CONF_ADJ_PARTIAL = +2`
- `THESIS_EVAL_MAX_CONF_ADJ_CONFLICT = -10`
- `_STALE_THESIS_TRANSITIONS` = frozenset of status strings that trigger stale-setup marking

## Test coverage
`test_thesis_phase3.py` — 31 tests across alignment classification (7 states), gate result fields, shadow/enforced/off mode invariants, stale detection, conf-adj caps, false-block/loss-avoided identification, stats structure.

Total thesis test suite: Phase 1 (16) + Phase 2 (17) + Phase 3 (31) = 64 tests.
