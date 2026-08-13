---
name: INTRADAY_TREND READY_REDUCED tiered verdict
description: Evidence basis, implementation details, and invariants for the READY_REDUCED verdict added to IT (Task #180 Checkpoint B).
---

## Evidence basis (Checkpoint A replay — 131 MNQ sessions, Jan–Jul 2026)

| Gate | n | ≥1R | ≥2R | avgMFE | avgMAE | Decision |
|------|---|-----|-----|--------|--------|----------|
| BLOCKED_EXTENSION | 12,053 | 72.7% | 49.6% | 2.76R | 3.15R | KEEP |
| HTF_CONFLICT_1H | 6,142 | 66.2% | 44.7% | 2.73R | 2.93R | KEEP |
| **AWAITING_CONFIRMATION** | **2,247** | **90.0%** | **80.2%** | **11.63R** | 9.20R | **→ READY_REDUCED** |
| INVALID_STOP | 1,850 | 57.3% | 40.8% | 28.32R | 22.06R | KEEP |
| BLOCKED_MID_RANGE | 3 | — | — | — | — | KEEP (n too small) |

Dominant cause was bad filtering (not late confirmation). Median remaining MFE after AWAITING_CONFIRMATION block = 6.23R. Only 9.4% of these bars had already moved ≥1R before being blocked.

## What was implemented

`_it_confirmation_complete` now returns `(complete, partial_ok, steps_done, missing)` — 4-tuple.

`partial_ok` is True when:
- LSR: sweep AND structure present, choch absent (2/3, both primaries done)
- BR: brk present AND exactly one of {vwap_c, structure} present (2/3)
- TP: trend_ok present AND exactly one of {vwap_c, reversal} present (2/3)

`partial_ok` is ALWAYS False when the primary trigger is absent (no valid setup).

`build_intraday_trade_plan` with `confirmation_partial=True`:
- Falls through all remaining hard gates (time, family, structural stop, chase, RR)
- Computes 50% of `MAX_RISK_DOLLARS` → floor to contracts; if 0 → `REDUCED_SIZE_UNAVAILABLE`
- Returns `it_ready_reduced=True`, `it_ready_reduced_missing=<first missing step>`

`full_analysis` IT branch emits `"LONG READY_REDUCED"` / `"SHORT READY_REDUCED"`.

## Hard blocks preserved (never softened by partial)
- INVALID_STOP (can't size)
- HTF_CONFLICT_1H (avgMAE > avgMFE)
- BLOCKED_EXTENSION (avgMAE > avgMFE)
- BLOCKED_MID_RANGE (n=3, inconclusive)
- Session/time gate, instrument gate (MNQ-only), IT_MAX_CHASE, IT_INSUFFICIENT_RR

## Key invariants

- `REDUCED_READY_VERDICTS = ("LONG READY_REDUCED", "SHORT READY_REDUCED")`
- NOT in `FULL_READY_VERDICTS` → never auto-fires
- `is_actionable()` returns True (ghost writes + manual entry eligible)
- READY_REDUCED is manual-only in this phase (not added to auto-fire arm check)
- `it_ready_reduced` and `it_ready_reduced_missing` are present in EVERY return dict from `build_intraday_trade_plan` (no KeyError risk downstream)

## Test coverage
- `test_intraday_trend_tiered_verdict.py` — 40 tests (all pass)
- `test_intraday_trend_phase2.py` — 120 tests (helper updated to strip partial_ok; existing 3-value unpack tests unchanged)
- `test_intraday_trend_phase3.py` + `test_intraday_trend_native_gate.py` — 139 tests (all pass)
- All 4 goldens pass (parity, scalp_golden, dual_sim, breakout_mode)

## gate_effectiveness IT extraction fix
`_extract()` now routes IT mode through an IT-native branch that reads `result["intraday_trend_context"]` for blockers/components instead of SWING-style `gate_debug.failed_conditions` / `comp_bos/choch` fields (which always reported `zone_valid` as the primary blocker for IT — wrong).

**Why:** `gate_debug` reflects the SWING strict gate fields that IT never populates. The correct IT blocker lives in `trade_plan.it_veto_code` or `it_ctx.status`.

**Trigger:** `isinstance(it_ctx, dict) and it_ctx.get("mode") == "INTRADAY_TREND"`

## Republish needed
- No DB migration required (ghost_observations schema already correct)
- Republish deploys the new verdict logic to production
