---
name: Advisory overlays — Stalk Mode + Active Trade Thinking
description: The two DISPLAY-ONLY observation overlays (pre-entry stalk + in-trade thinking) and the invariants that keep them out of the money path.
---

# Advisory overlays — Stalk Mode + Active Trade Thinking

Two display/advisory-only layers around (never inside) the strict engine. Flag-gated
(`STALK_MODE_ENABLED` / `ACTIVE_THINKING_ENABLED`, default ON for display). Stalk Mode =
pre-entry observation (potential direction, ideal entry zone, expected pullback, liquidity
target, why-waiting, extension warning). Active Trade Thinking = once a position is open,
grades thesis strength / 0-100 trade health / momentum / runner potential + exit/scratch
warnings and recommends ONE of HOLD / TAKE PARTIAL / MOVE STOP / WATCH CLOSELY / CONSIDER
EXIT. NO auto-exit — automation is explicitly future/out-of-scope.

**Why they're golden-safe:** the goldens (scalp/learning/swing-flagoff) snapshot the STRICT
CORE (evaluate_strict_setup + build_strict_trade_plan), and both overlays attach ABOVE strict
at the single `full_analysis` seam (alongside scalp_strategy_advisory / Main Brain), so they
are inherently outside the golden surface. Flag-OFF the key is simply ABSENT (not a disabled
block) on both `full_analysis` and `/status`, so OFF is byte-identical to today.

**How to apply / invariants any change here must keep:**
- State-aware compute = ONE attach covers open+vetoed+closed; do NOT add a separate
  closed-path mirror.
- Active Trade Thinking REUSES `compute_manual_trade_management`, which WRITES `min_r`/`max_r`
  back onto its input dict. MUST call it on a `dict(trade)` COPY (the trade dict is flat, so a
  shallow copy is enough) and pass `analysis=result` (no `full_analysis` recursion). NEVER pass
  the live `ACTIVE_TRADES_BY_INST` slot — that would mutate tracked state.
- Recommendation vocabulary is fixed at 5 verbs; map the proven manual-mgmt action onto them,
  never invent a 6th. `auto_exit` stays False.
- `ATT_PEAK_BY_KEY` is a display-only running-R cache (locked, size-capped); not money-path.
- Goldens do NOT exercise overlay-ON behavior — isolation is guarded by `check_stalk_active.sh`
  (money-path tripwire monkeypatches `_send_broker_order` + `requests.post` and asserts 0
  calls; flag ON keys present / OFF absent; no-mutation of the live trade slot; neutral schema
  is a superset of the computed schema; + node --check of the served dashboard `<script>`).
