---
name: Brain Conflict Resolver
description: 10-priority display-only conflict analysis engine living inside compute_main_brain; mb_out["conflict_resolver"]; hard vetoes → BLOCK, soft disagreements accumulate penalty → WAIT or ALLOW.
---

## Rule

`compute_brain_conflict_resolver(result)` checks 10 layers in strict priority order. Each layer is individually guarded so one bad key never cascades.

**Priority layers and veto type:**
| # | Layer | Trigger | Type |
|---|-------|---------|------|
| 1 | data_freshness | vwap_status=="stale" / vwap_value=None / market_data.stale | HARD |
| 2 | market_open | market_open=False | informational only |
| 3 | broker_health | live mode + execution_live=False | HARD |
| 4 | risk_veto | get_safety_cfg kill_switch / both longs+shorts blocked | HARD |
| 5 | prop_veto | prop enabled + active_id not in accounts list | HARD |
| 6 | position_state | active_trade.status in {error,invalid,orphaned} | HARD |
| 7 | learning_eligibility | GHOST_ONLY or disabled_setups present | HARD |
| 8 | mode_setup_rules | strategy_engine.ready==False | soft (WAIT only) |
| 9 | market_evidence | CVD vs structure (+15), LTF vs HTF (+20) | soft penalty |
| 10 | research_suggestions | SCALP vs MICRO_SCALP mismatch (+10) | soft penalty |

**Verdict logic:**
- `BLOCK` — any hard veto fires; highest-priority (lowest number) wins
- `WAIT` — soft penalty ≥ 30 pts OR market closed with soft disagreements
- `ALLOW` — all layers pass, or total soft penalty < 30 pts

**Return schema:**
```python
{
  "available":          bool,
  "verdict":            "BLOCK" | "WAIT" | "ALLOW",
  "hard_vetoes":        [{"priority": int, "layer": str, "reason": str}, ...],
  "soft_disagreements": [{"priority": int, "layer": str, "reason": str, "penalty": float}, ...],
  "confidence_adj":     float,   # always negative or 0.0 (reduces confidence)
  "priority_trace":     [{"priority": int, "layer": str, "passed": bool, "reason": str, ...}, ...],
  "reason":             str,
}
```

**Neutral schema:** `_bcr_neutral(reason)` — available=False, verdict="ALLOW", empty lists.

## Why

The Main Brain needed a structured, priority-ordered conflict analysis that surfaces WHY a trade is blocked or conflicted before it reaches the operator. The 10-layer priority ordering matches the user's stated spec exactly. Soft disagreements are additive penalties — they never split the conclusion into competing alternatives.

## How to apply

- Add a new hard veto: add a guarded `_layer(N, "key", condition, veto_type="hard", reason=...)` call inside its priority block.
- Add a new soft disagreement: use `veto_type="soft"` with a `penalty=` value. If cumulative ≥ 30 pts, verdict becomes WAIT automatically.
- To read BCR output: `result["main_brain"]["conflict_resolver"]` (same path as observations / synthesis).
- The BCR is DISPLAY-ONLY — it never touches the gate, sizing, dedupe, or money path. Never add a money-path side-effect here.
- `get_safety_cfg(inst)` is called inside a nested try/except; fail-open if it raises.
