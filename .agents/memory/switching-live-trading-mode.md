---
name: Switching the live trading mode (SCALP/SWING)
description: How to durably switch the live bot between SCALP and SWING, the production-only env gotcha, and how to tell "correctly quiet" SWING from "broken".
---

# Switching the live trading mode

The active profile is `TRADING_MODE` (env, default `SCALP`). `MODES["SCALP"]` vs `MODES["SWING"]` cfg blocks drive every mode-aware reader via `cfg()`.

## Durable switch
- `/mode` POST flips the in-memory global **only** — reverts to the env default on any restart/republish. Fine for an instant trial, NOT durable.
- Durable switch = set the `TRADING_MODE` env var, then **republish** (env is read at process boot; the already-running instance does not pick it up live).

## Set it PRODUCTION-ONLY, never "shared"
**Why:** the dev validation scripts `check_parity.sh`, `check_cross_market.sh`, `check_fvg_ob.sh` run with NO explicit `TRADING_MODE` and rely on the SCALP default; a `shared` `TRADING_MODE` would make them run under the other mode and fail vs their SCALP-captured baselines. (The golden/smoke scripts that matter set `TRADING_MODE=...` inline on the command, which overrides any inherited value, so they're safe either way.)
**How to apply:** `setEnvVars({values:{TRADING_MODE:"SWING"}, environment:"production"})`.

## SWING delivers "more selective + with-trend + better R:R" in one switch
- Selective: `EDGE_READY_THRESHOLD`/`EDGE_FULL_READY_THRESHOLD`=80 (vs SCALP 50/60), mandatory zone+vwap+structure, hard CVD + hard volatility gates.
- With-trend: `SWING_HTF_ENABLED` (default True) auto-computes 1H/4H/Daily bias+levels; `_swing_entry_veto_reasons()` fails CLOSED on stale/incomplete HTF or 1H/4H misalignment → won't buy into a downtrend. NOTE: it VETOES counter-trend entries; it does not manufacture shorts, so a one-sided LONG alert stream just yields more "no trade", not balanced long/short activity.
- Better R:R: `ENFORCE_MIN_RR=True` + `SWING_MIN_RR=2.0` (≥1:2).

## "Correctly quiet" vs "broken quiet" (the main SWING operational risk)
SWING can go near-silent legitimately (Edge<80, no fresh zone, opposing target <2R) OR because HTF data is missing. Distinguish via `/status`: `swing_diagnostics.enabled/complete/stale`, populated 1H/4H biases + daily level, plus `strict_reason` / `trade_plan.reason` / `/eval-metrics` rejection counters. If `complete=false`/`stale=true` persists minutes after republish, it's blocked by HTF data, not selectivity.
