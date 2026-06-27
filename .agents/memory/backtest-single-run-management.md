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
selects the EXIT model. Detectors/conflict-guard/news/vol/max-trades filters are
shared, but the FIXED-TP entry gates (`risk > tp1d`, `min_target_r` via `tp1d/risk`,
SWING `tp2d/risk`) are **LEGACY-ONLY** (see "Fixed-TP entry gates" below). Walk:
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

**Fixed-TP entry gates are legacy-only (MNQ 0-trades root cause):** `risk > tp1d`
(stop>target) + `min_target_r` (`tp1d/risk`) + SWING `tp2d/risk` use the FIXED spec
TP points — the legacy partial-TP model's literal levels, meaningless for R-based
models (target = N×risk). Because MNQ's ATR risk (median ~80pts) always exceeds its
fixed tp1=20, they rejected **100% of MNQ signals → 0 trades in EVERY config** until
fixed. Now they apply ONLY to BT_MGMT_LEGACY; R-based models use a management-MODEL-
INDEPENDENT 1.0R eligibility reference (live edge is 1:1), so `min_target_r > 1.0`
filters ALL R-based entries by design and ≤ 1.0 admits them. Dashboard `rn-minr` +
engine MIN_TARGET_R both default to 1.0 so the default UI run trades. Same gate
across R-based models, but realized trade lists still differ via exit timing + the
one-position loop. Never loosen by faking fills.

**How to apply:** any future "backtest numbers are bad / too strict" request — check
the management model and MIN_TARGET_R FIRST before touching detectors or filters.
Never loosen by faking fills or weakening worst-case same-bar resolution (stop
first); the honest levers are exit management and the spec-target entry filter.
