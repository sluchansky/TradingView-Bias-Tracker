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
