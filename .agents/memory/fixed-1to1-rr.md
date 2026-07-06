---
name: Fixed 1:1 R:R trade-plan model
description: Per-mode R:R model (flag-on SWING 1:4 wide-stop, SCALP 1:2, flag-off SWING legacy 1:1, ORB 1:4); the tick-grid serialization + stop-side direction invariants that keep every plan exact through the broker gateway.
---

# R:R trade-plan model (flag-on SWING 1:4 wide-stop, SCALP default 1:2)

**SWING is MODE-SPLIT by the HTF flag.** Flag-ON SWING (production runs `TRADING_MODE=SWING`)
targets **1:4** via a daily-structure scan (`_swing_rr_target`, see swing-htf-data-layer.md P4)
with **WIDE stops (2.25× ATR base / 2.75× elevated, `SWING_STOP_ATR_MULT`/`_HIGH`)**,
`SWING_MIN_RR=4.0`, and a **$250** per-trade risk cap. Flag-OFF SWING (env `SWING_HTF_ENABLED=0`
kill-switch, and dev which defaults to SCALP) stays **legacy 1:1 R:R with 1.5×/2.0× stops and the
$100 cap** — the immutable flag-off golden. **SCALP** now defaults to **1:2** via
`SCALP_RR2_ENABLED` (default ON, live-loss-reduction): the SCALP primary target/reward/
`rr_num`/`rr` and the staged management exits (TP1 2R, TP2 2.5R, runner 3R) all scale in
lockstep through shared helpers `_scalp_primary_rr` / `_scalp_rr_targets`, and the entry veto
`loss_le_first_target` uses the same helper so the geometry stays coherent. Env kill-switch
`SCALP_RR2_ENABLED=0` restores legacy SCALP 1:1 (TP1 1R / TP2 1.5R) — which is why the SCALP
golden pins the flag OFF and stays byte-identical. The older tiered TP1/TP2/TP3 ladder is
retired for the *plan*. The sanctioned ORB 1:4 exception below still overrides AFTER plan
construction (SCALP or SWING). Stop is computed first (ATR/structure/zone), snapped UP to whole
ticks; `RiskDistance = abs(entry - stop)`; legacy `TP = entry ± RiskDistance`.

**RR-display surfaces MUST derive from the plan's `rr_num`/`rr`, never hardcode "1:1".**
A hardcoded "R:R 1:1 · Expected Profit $risk" Discord-card line silently mislabels every
1:2 / 1:4 plan; read `entry["rr_num"]` / `entry["rr"]` and compute profit = risk × rr_num
(keep the `rr_num == 1.0` branch byte-identical to the old string).

## Min-stop is MODE-SPLIT: SWING hard-rejects, SCALP WIDENS to a floor
The minimum-distance guard in `_dynamic_stop_plan` is mode-split — and the two modes resolve a
too-tight stop in **opposite** directions:
- **SWING** still HARD-REJECTS (no trade) when the calculated stop is below `min_stop_pts`
  (MGC<5 / MNQ<20 / MES<4 / MYM<30 pts, env-tunable via `*_MIN_STOP_PTS`). The reject LOGIC is
  unchanged; only the ATR multiplier feeding the distance widened for **flag-on** SWING
  (2.25×/2.75× via `SWING_STOP_ATR_MULT`). Flag-off SWING keeps 1.5×/2.0× (byte-identical golden).
- **SCALP** now **WIDENS** a too-tight stop UP to a per-instrument floor instead of rejecting it:
  if `scalp_min_stop_pts > 0` and the raw distance < floor, SCALP sets distance = floor, recomputes
  the stop (entry − dist for Long, entry + dist for Short), then ceil-snaps to the tick grid and
  flags the new return key `min_floor_applied=True`. SWING's branch is the unchanged `_reject`.
  Floors are **hard-coded literals in `INSTRUMENT_SPECS` (`scalp_min_stop_*`), NOT env-tunable**:
  MGC 3pts/30t, MNQ 12pts/48t, MES 3pts/12t, MYM 20pts/20t — all exact tick multiples (snap clean)
  and under the $50 SCALP per-trade cap.
**Why this reversed:** SCALP previously had NO minimum (tight stops allowed as-is) and the user was
"losing every single trade" to stops tighter than instrument noise. Fix = widen the SCALP ATR
multipliers (0.75/1.25 → 1.5/2.0) AND add these WIDENING floors. Widening (never rejecting) keeps a
SCALP setup tradeable while guaranteeing a survivable stop; risk/size still flow from the (now
wider) distance so exact 1:1 holds. Floors stay non-env-tunable so a stale legacy
`*_SCALP_MIN_STOP_*` secret can never silently change them in the live prop account.
**How to apply:** any stop change — live OR the copied backtest `bt_stop_plan` — must keep this
split (SWING reject, SCALP widen-to-floor) AND keep live/backtest STOP parity (the parity test
checks STOP geometry ONLY — mult/ticks/risk/stop price; backtest TARGETS are an intentionally-
decoupled R-based sweep, `BT_MODES[mode]["stop_mult"]` must match live `SWING_STOP_ATR_MULT`, see
backtest-engine.md).
Do NOT "restore" the old SCALP no-minimum behavior, and do NOT re-add an env read for the SCALP
floor. SCALP rejections are still only: invalid/missing stop, wrong side of entry, zero/negative
distance, size>risk cap (sizing/gateway), zone consumed/mitigated.

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

## Per-trade risk ceiling — MODE-SPLIT (SCALP $50 / flag-on SWING $250), $50,000 account
`max_risk_cap()` resolves the ceiling: env `MAX_RISK_DOLLARS_PER_TRADE` WINS if set, else the active
mode's profile `MAX_RISK_DOLLARS` (SCALP 50 / SWING 250 — SWING raised from 100). It is a CEILING —
clamp DOWN only, never up. **Operator gotcha:** a stale `MAX_RISK_DOLLARS_PER_TRADE` env/secret
silently overrides BOTH modes, making the SWING $250 raise dead config — verify that env is UNSET in
prod after this kind of change. Pure helper `_risk_capped_contracts(stop_dist, point_value, account,
risk_pct)`: `budget = min(account*pct, max_risk_cap())`, `contracts = budget // (stop_dist*pv)`,
clamped to broker max; `over_cap` true when result < 1 (one contract already risks > the cap → stop
too wide — expect this MORE often now that flag-on SWING stops are 2.25× ATR).
`execute_trade_gateway` (the single money choke for BOTH manual `/traderspost` and
`_maybe_auto_execute`) returns **409 SKIP** when over_cap — it NEVER silently sends 1 contract over
the ceiling. `calculate_position_sizing` exposes `over_risk_cap/risk_cap/note` and shows 0 honestly.
The display-only Trade Idea Review uses a parallel `_review_user_risk_cap(mode)` (same env override,
own fallback) — keep it in sync if the profile cap changes.

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
