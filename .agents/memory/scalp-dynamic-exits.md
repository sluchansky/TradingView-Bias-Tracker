---
name: SCALP dynamic exits (staging + lifecycle architecture)
description: How SCALP multi-target/runner/delayed-BE replaces forced 1:1 — geometry staged off the broker primary, the exit lifecycle runs LOCAL/paper-only on the managed-trade watcher, and the Option-C rule that paper auto entries are tracked by the managed trade (never an ACTIVE_TRADE). The invariants any change must keep.
---

# SCALP dynamic exits — staging + lifecycle architecture

SCALP-only overhaul that replaces forced 1:1 with TP1~1R / TP2 1.5R / optional runner 2R +
delayed break-even. ALL behavior is gated on `SCALP_DYNAMIC_EXITS_ENABLED` via
`_scalp_dynamic_enabled(mode)` and a family of `SCALP_*` cfg knobs whose SWING values are inert.

## Two mode namespaces — do not conflate
`_scalp_dynamic_enabled(mode=None)` takes a **TRADING** mode (SWING/SCALP, defaults to
`TRADING_MODE`). `execution_is_live(mode)` / `resolve_execution_mode()` are the **EXECUTION**
mode (manual_only/paper/traderspost/pickmytrade). The dynamic lifecycle requires SCALP trading
mode AND a non-live execution mode. Passing an execution mode into `_scalp_dynamic_enabled` (or
vice-versa) is a real bug class here.

## Geometry is staged off the broker primary
- Target geometry lives as **nested `management` metadata** (tp2/tp3/runner/be_level/tp1_pct/
  tp2_pct/runner_pct) in `build_strict_trade_plan`. The **broker-facing primary**
  (`target1`/`target2`/`rr`/`rr_num`/`reward_points`) stays at fixed 1R.
- **Why:** the broker order sends ONE TP = `intent.target1` and flattens the whole position
  there. Multi-target/runner geometry must NEVER reach the gateway/intent/dedupe/sizing, or the
  local tracker would assume runner exposure a live broker already closed → desync. Dynamic
  exits drive LOCAL/paper tracking, alerts, journal, dashboard ONLY.
- `_scalp_dynamic_targets(direction, entry, risk, edge_score, mode)` is the ONE geometry source,
  called by both the money-path builder AND display `compute_scalp_quality` so they can't drift.
  Runner only when `edge_score >= SCALP_RUNNER_MIN_EDGE`, else its share folds into `tp2_pct`
  (runner/tp3/runner_pct stay None); `edge_score=None` degrades to no-runner.

## The exit lifecycle runs on MANAGED_TRADES, not ACTIVE_TRADES
- `_scalp_dynamic_lifecycle_enabled(mt)` (SCALP + master flag + `not execution_is_live` + the
  trade carries tp1/tp2/stop/risk geometry) routes `_evaluate_managed_trade_levels` →
  `_evaluate_dynamic_managed_levels`: **stop-first** (ambiguous bar resolves against the trade),
  then TP1 partial → delayed BE → TP2 → runner. Each leg books `leg_pct * leg_R` into
  `realized_r` once; the final leg closes via `_finalize_dynamic_close` → `_close_managed_trade`
  on the **blended** realized R. Outcome is derived from `realized_r`
  (Win>0 / Loss<0 / Breakeven==0); `exit_reason` ∈ stop / breakeven_stop / tp1 / tp2 / runner.
  `outcome=="Loss"` ⟺ a pre-TP1 `exit_reason=="stop"` (−1R).
- Delayed BE (`_maybe_move_be_to_entry`): move stop→entry only after TP1 AND
  (favorable close OR price reached 1R). Books ~0R on the BE-moved remainder via the ORIGINAL
  risk basis (`_dynamic_leg_r`), so TP1-then-BE = partial profit, not a flat loss.

## Option C — paper auto entries are tracked by the managed trade (the 1:1 RACE fix)
A SCALP dynamic PAPER (simulated, non-live) AUTO entry must NOT create an `ACTIVE_TRADE`.
- **Why:** an ACTIVE_TRADE is finalised by `check_trade_events` at a flat 1:1 the instant TP1
  prints, racing and overwriting the managed blended-R close — i.e. paper trades were still
  effectively forced to 1:1 (the S4 FAIL). ACTIVE_TRADES is one slot per instrument; MANAGED_TRADES
  is 1:N and already supports stacked SCALP setups, so it is the right authority.
- **How:** `send_live_ready_card` registers the managed trade BEFORE `_maybe_auto_execute` on the
  SAME webhook. `_maybe_auto_execute` gates `status=="simulated" and _scalp_dynamic_enabled() and
  not execution_is_live(mode) and _tag_dynamic_paper_managed_trade(inst, plan, setup_key)` → bump
  count, log, `return True` (skip the ACTIVE_TRADE slot). `_tag_…` matches the open managed trade
  by instrument + direction + nearest entry within tol (`risk_points` or 1% of entry) and stamps
  `auto_setup_key/auto_exec_status="simulated"/auto_opened_at`.
- **FAIL-CLOSED:** if no managed trade matches (e.g. a pre-existing active trade blocked
  registration), `_tag_…` returns False and `_maybe_auto_execute` falls through to the legacy
  ACTIVE_TRADE path — a paper position is never left untracked.
- **Post-outcome bookkeeping** at the end of `_close_managed_trade`, tag-gated on
  `auto_exec_status=="simulated"` (so SWING/live/manual_only/untagged are untouched), reproduces
  what `check_trade_events` did for the now-absent active trade: Loss → `AUTO_FIRED_KEYS.discard`
  (re-arm, under `AUTO_TRADE_LOCK`) + loss cooldown; Win → win cooldown (NO re-arm); Breakeven →
  nothing. Cooldown calls stay OUTSIDE `AUTO_TRADE_LOCK` (SAFETY_LOCK must never nest under it).
  Whole block is fail-open.

## Invariants any change must keep
- **SWING + flag-off + live byte-identical** — the dynamic block is gated out; verified by the
  SWING golden + the lifecycle/paper-auto smokes (SWING/live collapse to legacy single-TP).
- **No money-path leak** — geometry stays in `management`; the paper-skip branch can fire ONLY
  for `status=="simulated"` + SCALP + non-live. LIVE / non-SCALP / flag-off always create the
  ACTIVE_TRADE.
- **All `management` keys always present** (readers hard-index) — unused → `None`, never deleted.
- **Fail-closed cfg validation** — bad target ordering (0<TP1_R<=TP2_R<=RUNNER_R) or scale-out
  pcts not in [0,1] summing 1.0 → `no_plan("Invalid SCALP dynamic target configuration.")` before
  any mutation.
- **ORB is the one sanctioned single-target (1:4) exception** — `_apply_orb_target_override` must
  null the nested dynamic keys after rewriting tp1.

## Dashboard diagnostics (display-only)
- `scalp_diagnostics` is a curated `/status` block built by `_scalp_diag_block(a)` — it flattens
  `result["scalp_quality"]` and overlays live `be_moved`/`exit_reason`. It carries ZERO money
  authority; it only mirrors what the gate/lifecycle already decided. Fail-open to an inert
  disabled block so it can never break `/status` or the render.
- `_scalp_live_lifecycle(inst)` sources `be_moved`/`exit_reason`: prefer the OPEN managed trade for
  the instrument, else the most-recent trade **CLOSED TODAY (ET)** ranked by `closed_at`
  (registered_at only as fallback). **Why bounded to today + ranked by closed_at:** managed trades
  are housekept only at register time, so a prior-session close lingers in memory — ranking by
  registered_at (or no day bound) leaks a stale/misordered exit. ET day matches the journal's
  `closed_at AT TIME ZONE 'America/New_York'` convention.
- The dashboard panel is theme-var driven (`--text` = `#f3e9ff` default / `#86ffc2` retro). When a
  diagnostic value goes neutral, reset its inline color to `''` (re-inherit the theme var), NEVER a
  hardcoded hex — only semantic green/red status colors may override. A JS helper that sets color
  *only when truthy* leaves a stale green/red on the next poll (the S5 `_sdSet` bug → always assign
  `col || ''`).

## Test surface
`.local/state/scalp_dynamic_targets_smoke.py` (geometry), `scalp_dynamic_lifecycle_smoke.py`
(blended-R state machine + SWING/live collapse + idempotent frozen refresh),
`scalp_dynamic_paper_auto_smoke.py` (Option-C tag match/fail-closed/stacked + re-arm/cooldown
semantics + untagged untouched). SWING/SCALP golden + registry parity + veto/chop/quality smokes
are the regression net. Keep the paper-auto smoke in the validation set for any future
auto-exec / lifecycle edit.
