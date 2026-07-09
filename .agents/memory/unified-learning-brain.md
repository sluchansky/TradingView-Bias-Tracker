---
name: Unified Learning Brain
description: Per-mode (Scalp/Swing/Micro Scalp) learning stats + Playbook Selector + Unified Learning Memory panels; display-only cognitive seam.
---

## What was built
One central brain that evaluates all three playbooks (Scalp / Swing / Micro Scalp) and surfaces which mode is best for the current setup, why, what was rejected, and a consolidated learning memory.

## Key globals
- `PER_MODE_STATS = {}` — keyed by `(instrument, mode_upper)` → `{n, win_rate, avg_r, expectancy, wins, losses, top_failure, top_failure_key, top_failure_n}`
- Populated inside `_recompute_learning()` via a new `GROUP BY instrument, UPPER(COALESCE(trading_mode,'SCALP'))` query on `strategy_trades`
- Cleared + swapped under `LEARNING_LOCK` in the same atomic swap as `LEARNING_ANALYTICS`

## New functions (display-only, fail-open)
- `compute_playbook_selector(result)` — reads PER_MODE_STATS + LEARNING_ELIGIBILITY + _micro_ghost_stats() + _dual_sim_stats() (for shadow SWING data when live=SCALP); scores each playbook; picks best; returns selected_mode / why / rejected[] / final_decision
- `compute_unified_learning(result, inst)` — per-playbook stats grid + similar trade memory summary from result["trade_memory_context"]
- Both wired into `full_analysis` cognitive seam after `main_brain_voice` (display-only, never gate/score/broker)
- Both whitelisted in `/status` response

## Dashboard panels
- `mod-playbook-selector` — mode selected badge + final decision badge (LIVE/GHOST/WAIT/MANAGING) + why text + per-playbook cards + rejected reasons
- `mod-unified-learning` — tabbed (Scalp/Swing/Micro) stats grid + eligibility badge + top failure + Similar Trade Memory block; tab state in `_ulActiveTab`; re-renders via `window._lastStatusData` on tab click

## Safety invariants
- All DISPLAY-ONLY; `compute_playbook_selector` never changes TRADING_MODE or the execution path
- `_micro_ghost_stats(inst)` is already TTL-cached (safe to call in the /status path)
- `_dual_sim_stats()` is a separate DB read — only called when DUAL_MODE_SHADOW_SIM_ENABLED
- Goldens are byte-identical (new code is after the gate/score/exec path)

**Why:** User wanted ONE brain that shows per-playbook learning history and explains which mode it recommends and why, instead of 5+ scattered learning panels.
