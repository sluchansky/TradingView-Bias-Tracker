---
name: Fixed 1:1 R:R trade-plan model
description: All live trade plans use a fixed 1:1 risk:reward; the tick-grid serialization + stop-side direction invariants that keep it exact through the broker gateway.
---

# Fixed 1:1 R:R model

All live trade plans are **fixed 1:1 R:R** (the older tiered TP1/TP2/TP3 / MNQ 20·40·60 /
MGC 5·10·15 ladder is retired for the *plan*) — **EXCEPT the one sanctioned ORB 1:4 exception
below**. Stop is computed first (ATR/structure/zone), snapped UP to whole ticks;
`RiskDistance = abs(entry - stop)`; `TP = entry ± RiskDistance`. `build_strict_trade_plan` sets
`target1 == target2`, `rr = "1:1"`, `rr_num = 1.0`; `_decision_support` reward reads `"1:1 (fixed)"`.

## Min-stop is a HARD REJECT (mode-aware `min_stop_pts`), NOT a tick floor
`_dynamic_stop_plan` REJECTS a setup outright (no trade) when the calculated stop distance is
below the instrument's mode `min_stop_pts` — it NEVER silently widens to a floor. The companion
`min_stop_ticks` is **metadata only** (the snap is ceil-up to whole ticks). Values are mode-aware:
SWING MGC<5 / MNQ<20 pts; SCALP MGC<3 / MNQ<10 pts (all env-overridable).
**Why:** silently widening a too-tight stop changes risk/size behind the user's back and breaks
exact 1:1; rejecting is honest. **How to apply:** any stop change — live OR the copied backtest
`bt_stop_plan` — must keep the hard-reject + snap-only shape (see backtest-engine.md for the
parity contract). Don't reintroduce a `max(ceil, min_ticks)` floor.

## Sanctioned exception — a truly-ready Opening Range Breakout = 1:4 (user-approved)
`_apply_orb_target_override(result)` runs right after `compute_strategy_engine` and rewrites
ONLY `trade_plan` target1/target2 (`:.2f`), `rr="1:4"`, `rr_num=4.0`, `reward_points`, `reason`,
and `management.tp1`. entry/stop/direction/verdict are NOT touched (the strict gate still owns
those). FAIL-OPEN: any missing/mismatched field leaves the 1:1 plan untouched.
**Guards (ALL required):** `strategy_engine.active_key == "OPENING_RANGE_BREAKOUT"` AND
`strategy_engine.ready` AND actionable verdict AND valid `management.entry`/`risk_points` AND
engine direction == plan direction.
**Why `ready` is load-bearing:** `compute_strategy_engine` falls back to the highest-completeness
*eligible* strategy even when none is fully met (`engine["ready"]` == active strategy `fully_met`),
so without the `ready` guard a forming/fallback ORB under an otherwise-READY strict setup would
wrongly inherit 1:4. The fallback active_key must NOT earn the exception.
**Tracking parity:** every surface reads the SAME authoritative plan, so a real ORB is 1:4 on the
broker order AND in local tracking — gateway parses `target1`; `_maybe_auto_execute` uses gateway
`plan.takeProfit`; both local ENTER paths re-derive `t1 = entry ± rr_num*risk` (rr_num lifted from
the plan, default 1.0). Non-ORB is byte-identical 1:1 because base plan emits `rr_num=1.0`.

## $100/trade risk ceiling ($50,000 account)
`MAX_RISK_DOLLARS_PER_TRADE` (default 100, env-overridable) is a CEILING — clamp DOWN only, never
up. Pure helper `_risk_capped_contracts(stop_dist, point_value, account, risk_pct)`:
`budget = min(account*pct, hard_cap)`, `contracts = budget // (stop_dist*pv)`, clamped to broker
max; `over_cap` true when result < 1 (one contract already risks > $100 → stop too wide).
`execute_trade_gateway` (the single money choke for BOTH manual `/traderspost` and
`_maybe_auto_execute`) returns **409 SKIP** when over_cap — it NEVER silently sends 1 contract over
the ceiling. `calculate_position_sizing` exposes `over_risk_cap/risk_cap/note` and shows 0 honestly.

## Invariant 1 — serialize EVERY plan price with `:.2f`, never `:.1f`
**Why:** MNQ tick is **0.25**. `:.1f` rounds a valid quarter-tick (e.g. `30022.75`) to
`30022.8`, an **invalid tick**. The damage only appears at the money path:
`execute_trade_gateway` re-parses the `entry_zone` midpoint, `stop_loss`, and `target1/2`
*strings* to build the broker order, so a mis-rounded string silently breaks exact 1:1 and
sends an off-grid order. `:.2f` represents both grids exactly (0.25 and 0.10).
**How to apply:** any new f-string that prints a plan price (plan dict, Discord ENTER/paper/
sent announcements, dashboard) must use `:.2f`. Internal `%.1f` *loggers* are fine to leave.

## Invariant 2 — snap entry to the tick grid, re-centre the zone band symmetrically
`build_strict_trade_plan` snaps `entry = round(round(mid/tick)*tick, 10)` and sets the
displayed band `lo, hi = entry - buf/2, entry + buf/2`.
**Why:** the gateway reconstructs entry as `(lo + hi)/2` from the string band. Symmetric
re-centre makes that round-trip to *exactly* `entry`. `stop_buf/2` is a whole-tick multiple
for BOTH instruments (MNQ 2.5 = 10 ticks, MGC 0.5 = 5 ticks), and the stop distance from
`_dynamic_stop_plan` is already whole-tick, so entry/stop/TP all land on-grid and exactly 1:1.
**How to apply:** don't replace the symmetric band with an asymmetric `[anchor, anchor+buf]`
or you reintroduce a half-tick midpoint drift through the gateway.

## Invariant 3 — ENTER tracking derives direction from the stop side, not a defaulted "Long"
Both ENTER tracking paths (`_handle_command_alert` ENTER branch and the `/enter` route)
infer `direction = "Long" if stop < entry else "Short"`, reject `entry == stop`, and reject an
explicit direction that contradicts the stop placement; then recompute `t1 = entry ± rr_num*risk`,
`t2 = t1` (rr_num lifted from the authoritative plan: 1.0 for all strategies, 4.0 for a ready ORB).
**Why:** `direction` used to default `"Long"` when omitted, so a Short ENTER would place the
1:1 TP on the *stop* side while announcing "(1:1)" and mis-tag `ACTIVE_TRADE` (consumed by
`compute_pnl` / `compute_distances` / `check_trade_events`). Stop side is the bracket's ground
truth. These ENTER paths are LOCAL tracking + Discord only — the broker money path is
`/traderspost → execute_trade_gateway`, which is server-authoritative (re-runs `full_analysis`,
`ready_direction(verdict)`), so it stays 1:1 on its own.
