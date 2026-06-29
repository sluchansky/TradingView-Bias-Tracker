---
name: R:R exit-management sweep (breakeven-runner leak)
description: Empirical verdict on whether stricter entries justify larger R:R or a breakeven-runner; why the "bigger R:R wins" finding does not generalize and why the live path already has no leak.
---

A clean full-sample SCALP sweep (identical entries, only the exit model varies — `backtest_engine.run_backtest` across target_1r / target_1_5r / target_2r / be_after_1r / partial_1r_runner_2r / partial_tp3 on MGC + MNQ 5m datasets) showed:
- **EVERY** exit model is net-NEGATIVE on the available data — no target rescues entries that lack edge.
- **be_after_1r and partial_1r_runner_2r** (the "let the runner ride behind a breakeven stop" models = the proposed leak *fix*) are the **WORST** (win rate craters to ~18-20%). Breakeven-arming HURTS.
- Among fixed targets, **1.0R / 1.5R beat 2.0R**. "Bigger R:R" did NOT help on the full sample.

**Why:** the prior "target_2r ≈ doubles net R vs legacy" came from DB backtest runs (e.g. run 31) created under an OLDER/different entry gate that admitted only a ~29-trade favorable subset. Current engine: for R-based models the entry reference is hard `entry_ref_r = 1.0`, so `min_target_r` is binary (≤1.0 = all trades, >1.0 = zero trades) — the favorable subset is NOT reproducible. The subset finding was an artifact, not a generalizable edge.

**Also:** the LIVE money path already has NO breakeven-runner leak — SCALP sends a single clean ~1R TP (`SCALP_RR2_ENABLED` OFF, `LIVE_RUNNER_ENABLED` OFF). The BE round-trip only exists in the deprecated backtest legacy (`partial_tp3`; single-run default already moved to `target_1_5r`) and the paper/scoreboard sim.

**How to apply:** do NOT implement a BE/runner "leak fix" or bump live R:R on the strength of a filtered subset — per the clean sweep it would lose money. The real lever is entry edge, not exit target. Always re-confirm with a full-sample, identical-entry sweep (`.local/state/rr_sweep.py`) before any exit-management money-path change.
