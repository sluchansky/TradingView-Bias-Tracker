---
name: Unified Learning Brain
description: Observation bus + reconciliation layer that collects all specialist engines into ONE recommendation + ONE narrative. Plus per-mode learning stats and playbook selector panels.
---

## Architecture (current)

### Observation bus layer (added on top of original)
Three new pure functions inserted BEFORE `compute_main_brain`:
- `_mb_observe(engine, stance, confidence, key_finding, veto, weight)` — normalises any engine output to a standard dict
- `_mb_collect_observations(result)` — reads gate + analyst + trade_debate + pro_review + confidence_governor + entry_quality + volatility; fail-open per engine
- `_mb_reconcile(observations, result)` — weighted vote → ONE `recommendation` (TAKE/CAUTION/WAIT) + ONE `narrative` + `conflicts` + `playbook` + `supporting_engines` + `opposing_engines`

Called at the end of `compute_main_brain` (after scalp_strategy_advisory fold-in):
```python
_obs = _mb_collect_observations(result)
_unified = _mb_reconcile(_obs, result)
mb_out["unified"] = _unified
if _unified.get("available") and _unified.get("narrative"):
    mb_out["summary"] = _unified["narrative"]   # replaces text-assembled summary
```

### Key engine key names in `result` dict
- `result["analyst"]` — analyst reasoning
- `result["trade_debate"]` — NOT "debate"; has `final_verdict`, `judge_summary`, `veto_would_fire`
- `result["confidence_governor"]` — NOT "governor"; has `allow_trade`, `confidence_adjustment`
- `result["pro_review"]` — professional review; has `score`, `grade`, `veto_would_fire`
- `result["entry_quality"]` — has `score`, `location_label`, `veto_would_fire`, `chasing_warning`
- `result["volatility"]` — has `regime`, `brake_applied`, `blocked`
- `result["strategy_engine"]` — has `active_strategy` for playbook naming

### Dashboard display
- `#mb-unified` div in `#mod-brain` (just below avatar section, above liquidity focus)
- JS in `renderMainBrain`: reads `mb.unified`, shows recommendation + confidence + narrative + engine vote chips + conflict alert
- `mb.unified` is already in the `/status` whitelist via the wholesale `main_brain` pass

### Recommendation values
TAKE=#22c55e, CAUTION=#f59e0b, WAIT=#6b7280 (same as gate colours for consistency)

### Safety invariants
- DISPLAY-ONLY; fail-open at every stage (try/except around every engine block)
- Gate observation is weight=2.0 (ground truth); advisory engines 0.5–0.9
- `veto_would_fire` from ANY engine → recommendation becomes WAIT regardless of gate
- Never touches gate, sizing, dedupe, traderspost, or any money path
- Goldens byte-identical (all new code is after the gate/score/exec path)

## Original per-mode learning layer (unchanged)
- `PER_MODE_STATS` global keyed by `(instrument, mode_upper)` from strategy_trades
- `compute_playbook_selector(result)` — scores SCALP/SWING/MICRO playbooks; display-only
- `compute_unified_learning(result, inst)` — per-playbook stats grid; display-only
- Dashboard panels: `mod-playbook-selector`, `mod-unified-learning`

**Why:** User wanted one brain that consults every engine and tells ONE story instead of making them read 6+ sub-panels. Reconciliation is purely additive to the existing architecture.
