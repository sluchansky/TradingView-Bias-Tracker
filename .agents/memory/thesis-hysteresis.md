---
name: Persistent market thesis + hysteresis (Phase 1)
description: Confidence-inertia layer wrapping evaluate_strict_setup(); prevents READY↔WAIT flip-flopping; fail-open, flag-gated, 16 tests pass.
---

# Persistent Market Thesis + Hysteresis — Phase 1

## The Rule
`_apply_thesis(inst, strict, raw_verdict, trade_plan)` is the single entry point.
It wraps `evaluate_strict_setup()` output and returns `(adjusted_verdict, thesis_snapshot)`.
Hooked in `full_analysis()` just before `result = dict(...)` is built.

## Key thresholds (env-overridable)
- `READY_THRESHOLD=75` — minimum confidence to promote to READY
- `HOLD_READY_THRESHOLD=60` — hold READY while confidence ≥ this after any drop
- `REVERSAL_THRESHOLD=85` — opposite-direction score required to flip the thesis
- `MAX_CONF_DROP=15` — maximum confidence fall per evaluation (inertia)
- `REVERSAL_COOLDOWN_MS=20000` — post-invalidation cooldown period

## State machine statuses
NEUTRAL → FORMING_LONG / FORMING_SHORT → READY_LONG / READY_SHORT → INVALIDATED → COOLDOWN → (reset)

## Hard invalidations (immediate, bypass hold band)
- Zone consumed (SWING only, zone_req=True) via `zone_broken_active` or `zone_valid` in missing
- Structure lost with no direction (direction=None + `structure_confirmed` in missing)

## Reversal logic (critical fix)
- Score < REVERSAL_THRESHOLD: weaken existing thesis (confidence -= MAX_CONF_DROP), keep direction
- Score >= REVERSAL_THRESHOLD: reset `prev=None` BEFORE `needs_new` check → fresh `_thesis_blank()` → new thesisId

**Why:** Without the reset the code fell into the `t = dict(prev)` branch and reused the old thesisId even after a confirmed flip.

## Integration points (all 4 complete)
1. `full_analysis()` — hook before result dict, exposes `result["thesis"] = _thesis_snap`
2. `_build_status_payload()` — `"thesis": a.get("thesis")` at end of return dict
3. `create_journal_entry()` — thesisId / confidenceAtEntry / thesisAgeAtEntry / evidenceFor / evidenceAgainst / invalidationReason stamped at entry time
4. Market-closed override — no neutralisation needed (thesis result already computed before the closed block)

## Flag-OFF
`THESIS_HYSTERESIS=0` (env) → `_THESIS_ENABLED=False` → `_apply_thesis` returns `(raw_verdict, {})` immediately. Existing behaviour byte-identical.

## Tests
`test_thesis_hysteresis.py` — 16 tests, all pass. Pure-function (no network/DB).
Run: `python3 test_thesis_hysteresis.py` from the `artifacts/tradingview-webhook/` dir.
