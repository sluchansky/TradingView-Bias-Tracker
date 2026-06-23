---
name: Dual-timeframe SCALP engine
description: Flag-gated 1m-bias + 5s-execution entry engine — invariants, money-path branch, and the source-param bug class.
---

# Dual-timeframe (1m bias + 5s execution) SCALP engine

Additive entry model layered on top of (never replacing) the existing scoring, behind
env flag `DUAL_TF_ENGINE` (default OFF = byte-identical no-op) and SCALP-only. SWING
money path is never touched.

## Bias model (operator-confirmed)
- **1m bias setters: BOS/CHOCH (authoritative — set / flip / opposite-expire) + Supply/
  Demand zones (confluence — bootstrap a bias ONLY when none stands, refresh age).**
- **VWAP is NOT a bias source AND (post-rewrite) NOT a confirmation either.** Bias stays
  structural (BOS/CHOCH + zones). **Why:** the spec also said "after expiry wait for new 1m
  *structure*", and a continuous price-vs-VWAP setter would thrash + re-arm right after expiry.
  If a future request wants VWAP-position bias, implement it as confluence-bootstrap (like
  zones), not an authoritative flip, and reconcile with the post-expiry "wait for structure" rule.
- **5s READY = CONVERGENCE (operator-chosen):** standing bias + ≥2 distinct fresh ALIGNED
  confirmations within `DUAL_TF_CONFIRM_WINDOW_SEC` (10s). **NO entry-trigger required** — the
  `trigger` field is now vestigial (still recorded, NEVER read for readiness). Confirmation
  categories are **CVD / sweep / volume ONLY**; `_dual_tf_signal` returns `kind=None` for
  VWAP reclaim/reject and DELTA spike so they CANNOT count. Volume is directionless (always
  agrees); opposite-direction CVD/sweep is filtered out by `_dual_tf_fresh_confirms`, not counted.
- Expiry: opposite 1m BOS/CHOCH (`opposite_structure`) OR bias-TTL stale (`timeout_10min`);
  both clear the trigger + pending confirmations. Expiry is LAZY (on webhook + /status read),
  no timer thread.

## Money-path invariants (must hold for any future edit)
- **All order firing stays in the single-threaded `_webhook_worker`.** The fast lane (the
  webhook request thread) may ONLY record state + enqueue a `dual_tf_entry` job; it must
  NEVER call the auto-execute path. When flag ON + SCALP, the legacy inline SCALP auto
  trigger is disabled so there's no double-fire.
- The dual-TF gateway branch is AUTHORITATIVE: it builds the plan server-side from the
  standing bias + current price (not the Edge verdict), so it bypasses ONLY the Edge
  `is_actionable`/EARLY-tier checks. EVERY other money safety is shared and preserved:
  market/session/holiday, fixed 1:1 RR, $100/trade cap, daily cap, dedupe, fail-closed
  send. It also carries an opposite-direction Edge conflict veto. Full size only
  (`_size_mult` forced to 1.0 for the dual-TF source; the Edge structure_class reduced-size
  modifier is for the Edge-verdict paths only).

## Bug class to remember
- The auto-execute helper forwards a `source` arg to the execution gateway. **If the
  helper's own signature doesn't declare `source` (default "auto"), the LEGACY auto path
  raises `NameError` at call time even with the flag OFF** — i.e. it breaks the flag-OFF
  no-op invariant. It boots clean (the helper isn't called at import) so only a runtime
  auto-execute reveals it. **How to apply:** whenever you thread a new kwarg from a caller
  down into a gateway call, update the intermediate helper's signature in the SAME edit,
  and smoke-test the call path (both `source="auto"` and `source="dual_tf"`), not just boot.

## Brand-new alert names (additive; nothing replaced)
- New (added in TradingView): `ENTRY TRIGGER LONG/SHORT` (now VESTIGIAL — recorded, not
  required for readiness), `VWAP RECLAIM/REJECT`, `DELTA SPIKE BULLISH/BEARISH` (fast-ack but
  NEVER count as confirmations post-rewrite). Each accepts spaced, underscore, and
  `MGC/MNQ`-prefixed spellings; un-prefixed needs a `ticker` field.
- Reused (unchanged): BOS/CHOCH demand/supply, demand/supply zones, CVD bull/bear,
  volume spike, bullish/bearish sweep.
- The brand-new 5s types fast-ack and are kept OUT of the scoring lane; this is safe even
  flag-OFF because they are names the old system never used.

## Flag-OFF byte-identical no-op — two non-obvious traps
- **Reused names need a dormant guard, brand-new names don't.** CVD/volume/prefixed-sweep
  are LEGACY names with existing behavior, so gating the engine alone isn't enough: a BARE
  `BULLISH_SWEEP`/`BEARISH_SWEEP` would otherwise stay "recognized" and change the legacy
  flow. Fix = `_dormant_sweep = normalized in DUAL_TF_SWEEP_TYPES and not (DUAL_TF_ENGINE and
  SCALP)`, folded into the unknown-type guard so dormant bare sweeps fall back to legacy
  "unrecognized". **How to apply:** any time the engine reuses an existing alert name, prove
  the flag-OFF path is unchanged for THAT name, not just that the engine no-ops.
- **Write the data store BEFORE enqueuing the entry job.** The dual_tf_entry worker re-derives
  a FRESH full_analysis, so the triggering CVD/volume must be committed first. CVD/volume
  ingestion was moved ABOVE the dual-TF enqueue block; both now assign `_data_only_resp`
  (returned after the block) instead of returning early. Storing after the enqueue is a race.
