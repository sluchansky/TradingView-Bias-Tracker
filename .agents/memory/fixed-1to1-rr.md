---
name: Fixed 1:1 R:R trade-plan model
description: All live trade plans use a fixed 1:1 risk:reward; the tick-grid serialization + stop-side direction invariants that keep it exact through the broker gateway.
---

# Fixed 1:1 R:R model

All live trade plans are **fixed 1:1 R:R** (the older tiered TP1/TP2/TP3 / MNQ 20·40·60 /
MGC 5·10·15 ladder is retired for the *plan*). Stop is computed first (ATR/structure/zone,
floored at min ticks); `RiskDistance = abs(entry - stop)`; `TP = entry ± RiskDistance`.
Plans below the min stop (MGC < 5 pts, MNQ < 20 pts) are rejected outright (no trade).
`build_strict_trade_plan` sets `target1 == target2`, `rr = "1:1"`; `_decision_support`
reward reads `"1:1 (fixed)"`.

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
explicit direction that contradicts the stop placement; then recompute `t1 = entry ± risk`,
`t2 = t1`.
**Why:** `direction` used to default `"Long"` when omitted, so a Short ENTER would place the
1:1 TP on the *stop* side while announcing "(1:1)" and mis-tag `ACTIVE_TRADE` (consumed by
`compute_pnl` / `compute_distances` / `check_trade_events`). Stop side is the bracket's ground
truth. These ENTER paths are LOCAL tracking + Discord only — the broker money path is
`/traderspost → execute_trade_gateway`, which is server-authoritative (re-runs `full_analysis`,
`ready_direction(verdict)`), so it stays 1:1 on its own.
