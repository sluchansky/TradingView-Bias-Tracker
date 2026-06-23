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
- **VWAP is NOT a bias source.** The operator explicitly chose to keep bias structural
  (BOS/CHOCH + zones) and use VWAP reclaim/reject ONLY as a 5s confirmation.
  **Why:** the spec also said "after expiry wait for new 1m *structure*", and a
  continuous price-vs-VWAP setter would thrash + re-arm right after expiry. If a future
  request wants VWAP-position bias, implement it as confluence-bootstrap (like zones),
  not an authoritative flip, and reconcile with the post-expiry "wait for structure" rule.
- 5s READY = standing bias + a REQUIRED aligned entry-trigger + ≥2 distinct fresh
  confirmations agreeing with the bias (directionless categories like volume always agree).
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
- New (must be added in TradingView): `ENTRY TRIGGER LONG/SHORT` (required trigger),
  `VWAP RECLAIM/REJECT`, `DELTA SPIKE BULLISH/BEARISH`. Each accepts spaced, underscore,
  and `MGC/MNQ`-prefixed spellings; un-prefixed needs a `ticker` field.
- Reused (unchanged): BOS/CHOCH demand/supply, demand/supply zones, CVD bull/bear,
  volume spike, bullish/bearish sweep.
- The brand-new 5s types fast-ack and are kept OUT of the scoring lane; this is safe even
  flag-OFF because they are names the old system never used.
