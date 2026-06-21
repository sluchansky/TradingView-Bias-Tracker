---
name: Backtest optimization study (parameter sweep)
description: How the research-only /backtest/optimize sweep is structured — what is actually simulated vs subset post-hoc, the BT-score↔live-Edge alignment requirement, and the ranking/eligibility semantics.
---

# Optimization study (research-only parameter sweep)

`run_optimization` in backtest_engine.py sweeps 6 dims (score 65–85, session/hour, 5m/15m
trend, RVOL volume, grade, 5 management models) over 4 strategies (Exhaustion Fade excluded)
and ranks combos by Net R → Profit Factor → smaller Max DD.

## Only strategy × management is simulated; everything else is a post-hoc subset
- The ONLY dims that change entry/exit PRICES are strategy and management model, so the engine
  simulates just those (≈ strategies × 5 mgmt) once and tags every candidate trade. Score /
  session / trend / volume / grade are in-memory FILTERS over the tagged trades — never re-sims.
- **Why:** collapses an ~80k brute force into a few dozen simulations while staying causal.
- **How to apply:** a NEW dim that does NOT move fill prices must be a subset filter; a dim that
  DOES move prices (e.g. a different stop model) needs its own simulation pass.

## BT score MUST track the live Edge Score
- The swept "BT score" thresholds are only meaningful if `_bt_edge_score` uses the SAME component
  weights as live `EDGE_COMPONENTS` (BOS20/CHOCH20/VWAP15/Sweep15/Volume15/CVD15/Session10 = 110).
  Volume confirmation is proxied by RVOL ≥ ~1.5 (the live volume-spike feed can't be replayed);
  zone proximity & confirmation candle do NOT score (retired from live).
- **Why:** the first build copied a STALE max-120 model (zone+25 / candle+10 / RVOL ± modifier)
  from a memory index hook, so score/grade rankings silently diverged from the live gate — caught
  only in architect review.
- **How to apply:** if live EDGE_COMPONENTS / EDGE_SCORE_MAX change, update `_bt_edge_score` +
  its self-test (test 6: full-confluence = 110, no-signal = 0, zone/candle excluded) in lockstep.

## Ranking / eligibility semantics (intentional, not bugs)
- `min_trades` (default 10) gates best_by_strategy / best_by_session / worst. `best_overall` FALLS
  BACK to the best of ALL rows when no combo is eligible, so a tiny dataset still shows something
  (a result note states this). The ranked TABLE intentionally includes sub-threshold combos.
- Effective quality filter = `max(score_threshold, grade_floor)`. Row schema key for the score
  threshold is `"score"` (NOT `score_threshold`). `pf` can be the string `"inf"` (UI shows ∞);
  `pf_num` (1e9 for inf) is what ranking compares.

## Hard isolation (same contract as the backtester)
- Pure & read-only: imports nothing from app.py; no full_analysis / live globals / Discord / broker /
  strategy_trades writes / runtime DDL. `/backtest/optimize` is owner-only (NOT in OPEN_PATHS) and
  whitelisted in flask-proxy.ts. Never changes live strategy or money-path rules.
