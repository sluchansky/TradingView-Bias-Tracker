---
name: Dual-Mode Shadow Simulator
description: Passive always-on paper sim that replays the live READY gate under BOTH SCALP and SWING at once; display-only, walled off from the money path; fidelity invariants.
---

# Dual-Mode Shadow Simulator

A passive, ALWAYS-ON paper simulator that replays the bot's REAL READY decision under
BOTH rulebooks (SCALP **and** SWING) simultaneously, regardless of the live
`TRADING_MODE` / arming, into its own ledger (`dual_sim_trades`) and a side-by-side
dashboard panel. Flag-gated `DUAL_MODE_SHADOW_SIM_ENABLED` (default OFF). It is
DISPLAY-ONLY and fully walled off from the live money path.

## Money-path isolation (the whole point)
- Writes ONLY `dual_sim_trades`. NEVER writes `strategy_trades` / `MANAGED_TRADES` /
  `ACTIVE_TRADES_BY_INST` / `AUTO_FIRED_KEYS`, never sends to a broker, never mutates
  the live `result` (verdict / trade_plan / edge_score).
- Flag OFF ⇒ `full_analysis` attaches NO `_dual_sim_inputs` key ⇒ byte-identical; the
  observer `_maybe_observe_dual_mode_sim` is a hard no-op. The strict-gate goldens do
  NOT snapshot this layer (it lives above the strict funcs), so it has its OWN smoke.
- Observer runs only on `source=="webhook"` + flag + `DUAL_SIM_DB_READY` + market open;
  fail-open. No in-app DDL (table made via the database tool / Publish schema-diff).

## Fidelity: active mode replays the LIVE verdict verbatim; other mode is a gate-level what-if
The observer treats the two modes ASYMMETRICALLY, and this is deliberate:
- **ACTIVE mode (`mode == TRADING_MODE`):** consume `result["verdict"]` /
  `result["trade_plan"]` / `result["strict_direction"]` / `result["edge_score"]`
  DIRECTLY from the already-assembled live result. This is the authoritative,
  post-assembly decision, so the active-mode ledger inherits EVERY live layer for free —
  including the **default-ON Entry Quality veto** (which demotes actionable→WAIT AFTER
  result assembly) and the **sanctioned ORB 1:4 retarget** (`_apply_orb_target_override`,
  which rewrites the plan target). It can NEVER diverge from what the bot actually decided.
- **OTHER (non-live) mode:** `_shadow_strict_verdict(mode, **inputs)` — a PURE gate-level
  replay (`evaluate_strict_setup(mode=)` → swing_ctx (SWING only) →
  `build_strict_trade_plan(mode=)` → SCALP veto → SWING veto → zone-broken/mitigated
  overrides). It does NOT mirror the display-assembled layers (Entry Quality / ORB) — those
  need a fully-assembled per-mode result that only the live path produces. This side is an
  honest "what-if for the non-live rulebook," not a live decision.

**Why this split:** an earlier design ran `_shadow_strict_verdict` for BOTH modes; because
the EQ veto (default ON) lives ABOVE the strict gate and was not mirrored, the active-mode
shadow could OPEN when the live verdict was WAIT. Re-deriving EQ/ORB in the shadow is
infeasible — `compute_entry_quality` consumes the whole assembled `result`, not the raw
inputs. Routing the active mode through the live result is both simpler and perfectly faithful.
**How to apply:** if you add a new live-verdict layer ABOVE the strict gate, the ACTIVE
mode picks it up automatically (it reads the final result). For the OTHER mode you only need
to mirror **gate-level** changes inside `_shadow_strict_verdict`; assembled-display layers are
intentionally out of scope there. A smoke (`eq_veto_*`) pins the EQ case: force the live EQ
veto and assert the active mode opens nothing while the other mode still opens.

**The asymmetry needs the live mode SNAPSHOTTED, not read live.** "Active mode" =
`mode == live_mode`. If the observer read the global `TRADING_MODE` at observer time, a
`/mode` flip between `full_analysis()` and the (later) webhook observer would swap the roles:
the real live mode would be re-derived via the gate-level shadow (EQ/ORB NOT mirrored) and
could OPEN even though the live verdict was a WAIT. So `full_analysis` stashes
`result["_dual_sim_live_mode"] = TRADING_MODE` (a SIBLING key, NOT inside `_dual_sim_inputs`,
so the `**inp` splat into `_shadow_strict_verdict` stays signature-exact) and the observer
uses `result.get("_dual_sim_live_mode") or TRADING_MODE`. Flag OFF ⇒ key absent ⇒
byte-identical. The `race_*` smoke pins it: flip the global after analysis and assert the
opened modes are unchanged.

## Two drift traps the smoke pins
- The `result["_dual_sim_inputs"]` stash keys must EXACTLY match the
  `_shadow_strict_verdict(**inputs)` keyword signature — a missing/renamed key silently
  breaks the replay (TypeError → fail-closed WAIT, sim quietly stops opening). The stash
  is captured BEFORE the zone-broken/mitigated blocks mutate confidence; the shadow
  re-applies those overrides itself from the same module globals.
- `evaluate_strict_setup` is now mode-parameterized (`mode=None` → `TRADING_MODE`) via a
  LOCAL `cfg()` that shadows the module-global `cfg()`; `mode=None`/default-mode is
  byte-identical (goldens pin it).
- The "pure" shadow must stay TRULY non-mutating. `is_near_mitigated_zone` reads the GLOBAL
  `cfg("MITIGATED_TTL_MIN")` and PRUNES `MITIGATED_PRICES_BY_TICKER` in place — so the SWING
  shadow (zone-gate path) would mutate shared global state with the wrong mode's TTL. It now
  takes `mode=None, prune=True` (default = byte-identical); the shadow calls it
  `mode=mode, prune=False` (read-only, mode-correct TTL). Any other money-path helper the
  shadow reuses must be audited the same way: read-only + mode-explicit, or it leaks.

## TEST trigger: open on Edge Score alone (`DUAL_SIM_TEST_EDGE_MIN`)
- Env-tuned floor `DUAL_SIM_TEST_EDGE_MIN` (default **0 = OFF**). When `>0`, the observer
  opens a shadow paper trade for any mode that is **NOT** actionable but whose Edge Score
  `>= floor` — i.e. it deliberately BYPASSES the strict structure/VWAP/zone gate so the
  simulator "takes trades" purely on edge, for testing/demo. Now set to `70` (shared env)
  so the ledger captures the full 70+ range for the 70/80/90 display-tier comparison (see
  "Display Edge tiers" below). **This is the ONLY thing that opens a shadow trade on a
  non-actionable verdict** — normal opens still require actionable + real plan.
- **Why it's still isolated:** the trigger only calls read-only helpers
  (`build_strict_trade_plan`, `compute_swing_context`, `_swing_htf_enabled`) + the same
  `_dual_sim_open_insert` into `dual_sim_trades`. It NEVER touches the gate / verdict /
  trade_plan / edge_score / broker / auto-execute — the sim stays display-only.
- **How to apply / gotchas:** default 0 ⇒ branch unreachable ⇒ byte-identical (the strict
  goldens never reach the observer anyway). `check_dual_sim.sh` **exports
  `DUAL_SIM_TEST_EDGE_MIN=0`** so the golden always runs the OFF baseline regardless of the
  shared env (which is set to 80); `dual_sim_smoke.py` section 7 pins the ON path
  (opens-when-WAIT, money-path untouched, result unchanged, below-floor no-op). Direction
  is derived from `strict_direction`/shadow `direction`, else unambiguous bullish XOR
  bearish; a failed `build_strict_trade_plan` fails closed (no open). Test opens SHARE the
  normal (mode,inst,dir) 300s cooldown + 5-min-bucket dedup, so a test open can throttle a
  later REAL actionable open of the same key (harmless — display-only ledger).
- **dev vs prod:** the observer only fires on real `source=="webhook"` events, so the dev
  instance won't show test trades (no live TradingView feed) — it takes effect on the
  PUBLISHED instance after a republish (picks up code + shared env; Publish schema-diff must
  ensure `dual_sim_trades` exists in prod). The floor is intentionally kept at `70` for the
  ongoing 70/80/90 tier comparison; reset `DUAL_SIM_TEST_EDGE_MIN=0` only when done, or it
  keeps opening a paper trade on essentially every 70+ WAIT.

## Display Edge tiers (70/80/90) — `DUAL_SIM_DISPLAY_TIERS`
- The dashboard shows three NESTED/OVERLAPPING cohorts (≥70 / ≥80 / ≥90) computed in
  `_dual_sim_stats` by filtering the SAME closed shadow trades on the `edge_score` stored at
  open (a 92-edge trade counts in all three tiers). Output gains
  `out[mode]["by_threshold"]={"70":..,"80":..,"90":..}` + top-level `out["thresholds"]`;
  purely additive/display-only, so the smoke's `isinstance(dict)` check and the strict
  goldens are unaffected. Renderer `renderDualSim` builds one table per tier via
  createElement/textContent (no innerHTML strings → no inline-JS escape trap).
- **Not exactly three independent sims.** `_dual_sim_open_insert` allows only ONE open trade
  per (mode,inst,dir) + a 300s cooldown, so with the floor at 70 an open 72-edge trade can
  BLOCK a later 95-edge setup on the same key from ever opening — a true standalone ≥90 sim
  would have taken it. Per-trade OUTCOMES within a tier are unbiased, but higher tiers
  UNDERCOUNT trades vs a real ≥80/≥90-floor sim. Present it as "the same paper trades
  re-bucketed by Edge quality," NOT as three separate simulations.
  **Why:** avoids opening 3× overlapping paper trades (which would clutter the ledger for
  zero extra outcome info) at the cost of higher-tier trade-count fidelity.
