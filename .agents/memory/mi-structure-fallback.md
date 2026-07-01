---
name: MI confidence-as-structure fallback
description: How strong one-sided Directional Confidence may SATISFY the structure gate (SCALP-only, money-path) without weakening risk — and the fail-closed freshness rules that make it safe.
---

# Market-Intelligence structure fallback (money path)

A flag-gated path (`MI_STRUCTURE_FALLBACK_ENABLED`, under master `MARKET_INTELLIGENCE_ENABLED`)
lets a strong, persistent, multi-confirmation one-sided **Directional Confidence** satisfy the
strict structure requirement, so the bot can auto-short a clearly-bearish tape (or long a bullish
one) with **no BOS/CHOCH/swing structure alert**. It lives in `evaluate_strict_setup`.

## The non-obvious invariants (don't break these)

- **It satisfies ONLY the structure boolean, never edge.** The Edge Score is computed from the raw
  alert flags (`has_bos_*` / `has_choch_*` / swing alerts), never from `structure_*`. So a fallback
  short earns ZERO BOS/CHOCH points. Practical consequence: max edge without real structure ≈ 70
  (VWAP+Sweep+Volume+CVD+Session) → only **SCALP** (READY 60 / EARLY 50) can fire; **SWING** (needs
  80) still effectively requires real structure. This is intended/conservative, not a bug.
- **Native `structure_long/short` stay untouched everywhere.** A separate overlay
  `structure_gate_long/short = native OR fallback` is consumed at EXACTLY two seams: the gate-debug
  `structure_confirmed` and the confluences `structure_confirmed`. Edge scoring, conflict
  timestamps, trend-memory, and SWING tie-break all keep reading the native vars. Widening the
  fallback to more consumers risks letting it weaken a *risk* gate instead of only the structure
  *requirement* — keep the two-seam discipline.
- **SCALP-only + live-mode-only.** Guard requires `str(m).upper()=="SCALP"` AND `m==TRADING_MODE`,
  because `build_strategy_context` reads the global `cfg()` (live mode). The dual-mode shadow sim
  passing the other mode must never open the real gate.

## Fail-closed freshness is the heart of the safety

Set-membership in the confidence "fresh" set is **NOT sufficient** for the required confirmations,
because the underlying per-instrument stores persist **indefinitely**:

- `CVD_BY_TICKER[inst]` and `RVOL_BY_TICKER[inst]` each carry a `ts`, but the strategy snapshot
  reads `.state` / `.value` **without** checking that `ts`. A stale CVD or a stale `RVOL>=threshold`
  can therefore sit in the "fresh" set forever.
- **Why:** an early version gated CVD and volume on set-membership alone; the architect flagged that
  a stale high RVOL could open the fallback. The fix: require BOTH `"cvd" in fresh` AND an explicit
  `_cvd_fresh` (CVD `ts` within `STAGE_WINDOW_MIN`), and BOTH `"volume" in fresh` AND a `_vol_fresh`
  (a fresh volume-spike via `_volume_spike_fresh`, OR an `RVOL>=RVOL_CONFIRM_THRESHOLD` whose record
  `ts` is within the stage window). `vwap`/`htf` come from per-eval computation so set-membership is
  enough for them.
- **How to apply:** any NEW required confirmation sourced from a long-lived per-instrument store must
  add its own `ts`-freshness check, not just rely on it appearing in a "fresh" set. Default every
  unknown/missing/exception to CLOSED.

The full predicate per side: `dom_conf >= MI_CONF_FLOOR`, `opp_conf <= MI_CONF_OPPOSITE_MAX`,
`margin >= MI_CONF_MARGIN_MIN`, trend-memory `persistence_ok` + `dominant_side` matches side +
`currentTrend` not opposite, the vwap/htf core present, plus the fresh-CVD and fresh-volume guards.

## CVD policy wording (avoid the trap)

In **SCALP**, CVD conflict is a **soft modifier**, not an independent hard veto (it IS hard in SWING).
So the safety against a CVD-conflicting fallback short is the fallback's **own** requirement of a
fresh, agreeing CVD — do not describe SCALP as having a standalone hard CVD veto.

## OFF-path parity

When `MI_STRUCTURE_FALLBACK_ENABLED` is off: both fallbacks are False, `structure_gate_* ==
structure_*`, and the new `structure_source` gate-debug key is suppressed (emitted via a conditional
dict-unpack only when the flag is on). Goldens are byte-identical; verify with the full check-script
suite after any edit here.
