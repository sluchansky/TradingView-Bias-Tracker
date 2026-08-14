---
name: IT Gate Audit Phase 1
description: First quantitative audit of INTRADAY_TREND gate-effectiveness — 134 production records, all BLOCKED, dominant blocker is zone_valid (77%).
---

## Key findings (134 IT records, production, all BLOCKED)

**Dominant blocker: `zone_valid` (103/134 = 77%)**
- VWAP passes 84% of the time
- Volume passes 84% of the time
- Edge avg = 42 on zone-blocked records (low but not zero)
- `comp_zone = UNAVAILABLE` on nearly all zone-blocked records — zone search returns empty, not rejection of a found zone
- IT is NOT finding S/D zones to anchor against; it never gets to the plan stage

**Other blockers (minor):**
- FORCE_FLAT: 15 (market closed / overnight)
- BLOCKED_DATA: 8 (no structure data available)
- structure_confirmed: 3 (BOS/CHOCH absent)
- edge_score / volume / cvd: 5 combined

**Counterfactual outcomes: zero**
- All 134 records are NO_GEOMETRY — no entry/stop/target ever computed
- Counterfactual watcher can only resolve records with geometry (i.e., after a plan is built, which requires zone_valid to pass)

**What this means:**
- Trend alignment, volatility, VWAP gates are NOT the problem for IT
- zone_valid is THE gate to investigate
- Question: is the IT zone search radius / detection logic returning empty when valid S/D zones exist nearby?

## IT gate recording fix (shipped)
- Before: `record_gate_decision(result, ticker, TRADING_MODE)` — IT overrides not captured
- After: `_eff_mode = getattr(_MODE_TLS, 'override', None) or TRADING_MODE` passed to recorder
- Effect: ModeOverviewPanel 30s polls (mode=INTRADAY_TREND) now accumulate IT records with correct mode label
- All 4 IT branch guards in full_analysis updated to use `_eff_mode` (not `TRADING_MODE`)

## GRE restart gap fix (shipped)
- `_restore_active_experiments()` now also restores `_active_opp[inst]` via lexicographic opp_id sort
- Before: mid-ORB restart silently dropped POSITION_ACTIVE/BLOCKED/BREAKOUT_MISSED transitions
- After: recovery logged with `_active_opp=list(self._active_opp.keys())`

## Expired trade fix (shipped)
- `_scalp_sim_stats()` now separates `expired` trades from `closed`
- expired increments only `a["expired"]` then `continue` — excluded from wins/losses/r_list
- Output includes `live_expired` counter so promotion score is not inflated by timeouts

**Why:** Expired trades (max-hold timeout) were counted in `a["closed"]` but not in `a["wins"]`/`a["losses"]`, distorting win_rate denominator and the `live_trades` promotion threshold.
