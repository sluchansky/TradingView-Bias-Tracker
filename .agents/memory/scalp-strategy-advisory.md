---
name: Scalp strategy advisory ("potential trades") Main-Brain layer
description: DISPLAY/ADVISORY-ONLY layer that analyzes all 16 research scalp strategies as ranked potential trades in the Main Brain; how it stays walled off from the money path and byte-identical when OFF.
---

# Scalp strategy advisory — analyze the 16 research strategies as "potential trades"

A DISPLAY/ADVISORY-ONLY Main-Brain layer that runs every research scalp strategy as a
*candidate idea* and grades it (quality label/score, entry/stop/target, R:R, confluence,
conflicts, recommendation, strict-alignment). It RANKS ideas for the operator — it does
NOT open, size, gate, dedupe, persist, or send anything.

- **Detectors are reused, not reinvented:** candidates come from the PURE
  `scalp_live_sim.build_candidates(lctx)` (16 testable detectors; 3 PENDING keys stay
  non-opened) via the read-only `_scalp_sim_live_ctx(result)`. The advisory wrapper only
  *enriches + ranks*; it never recomputes the gate.
- **Quality is derived ONLY from existing result fields** already on the assembled
  `result` (dominant_direction, current_price, vwap_value, cvd_direction, rvol_value,
  edge_score, swing_context bias) plus the candidate's own geometry/fidelity. No new
  market math, no recompute of strict scoring.
- **Ranking invariant:** strict-aligned (candidate direction == dominant_direction)
  candidates sort first, then by quality score / fidelity / R:R / key.

**Why this matters:** this is on a live-money bot. The whole value of the layer is that
it is provably advisory — a single stray call into the execution path would turn 16
"ideas" into 16 ways to fire an unwanted order.

**How to apply:**
- **Money-path wall (hard invariant):** the advisory functions must NEVER reference
  `STRATEGY_SCORERS`, mutate `evaluate_strict_setup`/the gate, or call
  `_maybe_auto_execute` / `execute_trade_gateway` / `_execute_traderspost` /
  `AUTO_FIRED_KEYS` / `strategy_trades` / learning writes / `scalp_strategy_sim_trades`
  writes / any INSERT/UPDATE/DELETE. It is read-only + display-only, FAIL-OPEN
  (context failure → neutral; per-candidate failure → skip; top-level → neutral).
- **Flag-OFF must stay byte-identical:** gated behind default-OFF
  `SCALP_MAIN_BRAIN_ADVISORY_ENABLED`. Byte-identity holds ONLY because the
  `result["scalp_strategy_advisory"]` key is *never attached* when OFF — guarded at all
  three seams: the attach before `compute_main_brain`, the `/status` dict-unpack, and the
  `main_brain["potential_trades"]` nesting. Adding any new surface must keep the same
  flag guard or the goldens break.
- **It has its OWN smoke** (`check_scalp_advisory.sh` → `scalp_advisory_smoke.py`,
  registered validation `scalp_advisory`) because the strict-gate goldens all run with
  the flag OFF and therefore do not exercise this layer. Verify dashboard JS with
  `node --check` on the served `<script>` (py_compile can't catch inline-JS breakage).
- Distinct from [scalp-live-sim](scalp-live-sim.md): that is the paper-sim *observer*
  (persists sim trades to its own table); this advisory layer persists nothing.
