---
name: Decision Pipeline V2
description: Shadow 5-stage decision architecture (Phase 1) — OBSERVE→INTERPRET→PRIORITIZE→VALIDATE→DECIDE; invariants, integration points, and future phase gates.
---

## Architecture
5-stage shadow pipeline runs at the `full_analysis` single return seam AFTER the production system's final verdict. Builds its own verdict independently from raw signal stores, then compares with live for agreement tracking.

**Stage contracts (strict separation — each stage knows nothing of the next):**
1. OBSERVE — read-only snapshot from global stores; NO interpretation
2. INTERPRET — bias (LONG_BIAS/SHORT_BIAS/NEUTRAL/CONFLICTED) + alignment%; NO gate logic
3. PRIORITIZE — ranked/weighted signal list; NO verdict
4. VALIDATE — 5 independent PASS/FAIL/INSUFFICIENT gates; NO combining
5. DECIDE — pipeline verdict + comparison with `live_verdict`; key Phase 1 metric is `agreement_with_live`

## Verdict Threshold (Stage 5)
- `n_insuf >= 3` → INSUFFICIENT_DATA
- `n_fail == 0 and n_pass >= 4` → READY (clean)
- `n_pass >= 3 and n_fail <= 1` → READY (1 fail tolerated)
- else → WAIT

## Signal Tiers and Mode Weights
| Signal | Tier | SCALP mw | SWING mw |
|--------|------|----------|----------|
| STRUCT | 3    | 1.5      | 2.0      |
| VWAP   | 2    | 1.0      | 1.5      |
| CVD    | 2    | 1.5      | 0.8      |
| SWEEP  | 1    | 1.3      | 0.8      |
| VOL    | 1    | 1.2      | 1.0      |

Gate 5 (conflict) only fails on **tier ≥ 2** opposing signals; low-tier conflict is a PASS.

## Global Stores Read (Stage 1)
- `AUTO_PRICE_BY_TICKER.get(instrument)` → price + age
- `VWAP_BY_TICKER.get(instrument)` → vwap_value + age
- `CVD_BY_TICKER.get(instrument)` → cvd_state ("bullish"/"bearish") + age
- `RVOL_BY_TICKER.get(instrument)` → rvol_value
- `VOLUME_SPIKE_BY_TICKER.get(instrument)` → freshness check vs cfg("VOLUME_SPIKE_TTL_MIN")
- `list(ALERT_HISTORY)` — GIL-safe snapshot; filters last 30 min per instrument

## Safety Invariants
- **NEVER** mutates any global state
- **NEVER** calls `full_analysis()` recursively
- Every stage wrapped in `_safe()` helper; fail-open to `{"status":"ERROR"}`
- Flag OFF → returns `None` → key never attached → goldens byte-identical
- `is_actionable()` used for live/pipeline READY comparison (not raw string match)

## Feature Flags
| Flag | Default |
|------|---------|
| `DECISION_PIPELINE_V2_ENABLED` | False (master gate) |
| `DECISION_PIPELINE_V2_SHADOW_MODE` | True |
| `DECISION_PIPELINE_V2_CAN_CHANGE_VERDICT` | False |
| `DECISION_PIPELINE_V2_CAN_CHANGE_CONFIDENCE` | False |
| `DECISION_PIPELINE_V2_CAN_CHANGE_RISK` | False |
| `DECISION_PIPELINE_V2_CAN_PAUSE_ENTRIES` | False |
| `DECISION_PIPELINE_V2_CAN_ROUTE_ORDERS` | False |

## Integration Points
1. Feature flags block — after `SWING_AUTO_EXEC_ENABLED`
2. `_dpv2_stage_observe/interpret/prioritize/validate/decide` + `compute_decision_pipeline_v2` — before `def full_analysis`
3. `full_analysis` seam — after `swing_v2` block, before TFA block; receives `instrument_of(active_ticker)`, `TRADING_MODE`, `verdict`, `strict_direction`
4. `_build_status_payload` dict — `"decision_pipeline_v2": a.get("decision_pipeline_v2")`
5. ADC tab button — `data-tab="dpv2"` after `mktenv`; panel `adc-panel-dpv2` / `adc-dpv2-content`
6. Render loop — `try{ renderDecisionPipeline(d); }catch(e){}` after `renderMarketEnv`
7. `renderDecisionPipeline(d)` function — before `hvsUpdateFromStatus`

**Why:** `_build_brain_state` is the correct seam anchor for inserting the pipeline function block (that function ends with `return {"available": False, "reason": str(_bse)}`).

## Future Phase Gates
- Phase 2: flip `_DPV2_CAN_CHANGE_VERDICT` → pipeline can demote READY → WAIT (veto-only)
- Phase 3: flip `_DPV2_CAN_CHANGE_CONFIDENCE` → pipeline adjusts displayed confidence
- Phase 4: flip `_DPV2_CAN_PAUSE_ENTRIES` → pipeline can suppress auto-execution
- Phase 5: flip `_DPV2_CAN_ROUTE_ORDERS` → pipeline drives order routing decisions
