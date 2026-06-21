---
name: Backtest single-run exit management
description: Why single-run backtest expectancy looked "really bad" and the management-selector / MIN_TARGET_R fix that addresses it without faking edge.
---

# Single-run backtest: management selector + MIN_TARGET_R

The single-run backtester (`simulate_strategy`) historically resolved every trade
with ONE self-capping exit: 50% off at the instrument's spec TP1, runner stop
jumps to breakeven, runner targets spec TP3. Net effect: most winners realize
~+0.5R (TP1 banked, runner stopped at BE) while losers are -1R, so expectancy is
negative unless win-rate is very high. This is the usual cause of "the backtest
returns really bad numbers" — it is a profit-taking artifact, NOT absence of edge.

**Fix (research-only, integrity-preserving):** `simulate_strategy(..., management=)`
selects the EXIT model only. ENTRY selection (detectors, conflict guard,
news/vol/max-trades filters, `risk > tp1d` reject, `min_target_r`, SWING rr gate)
is identical across all managements — only the walk differs:
- `BT_MGMT_LEGACY` ("partial_tp3") → existing `_walk_trade` (unchanged math).
- everything else → the optimizer's `_walk_managed` (fixed-R / partial+runner /
  BE-after-1R), returning `(exit_price, exit_bar, exit_reason, r_gross)`.
Default single-run management is `target_1_5r` (`BT_DEFAULT_MGMT`).

**Why:** letting winners run to a fixed/partial R target is a legitimate, more
standard exit than the BE-capped partial; it lifts net R on genuinely-profitable
combos (e.g. MGC VWAP-Trend +3.14R legacy → +4.67R @1.5R → +7.67R @2R) while
leaving no-edge combos negative (MNQ 1m Liquidity Sweep stays red under EVERY
model). Do NOT default to whichever model looks best on one strategy — there is
no universal best; the optimizer is the tool for per-combo selection.

**MIN_TARGET_R trap:** `min_target_r` gates on the instrument's FIXED first target
(`spec tp1 / risk`), not the R-based management target. MGC's spec tp1 is only
~1.0R, so a 1.5 default silently yields ZERO MGC trades. Keep the default ≤ 1.0.

**How to apply:** any future "backtest numbers are bad / too strict" request — check
the management model and MIN_TARGET_R FIRST before touching detectors or filters.
Never loosen by faking fills or weakening worst-case same-bar resolution (stop
first); the honest levers are exit management and the spec-target entry filter.
