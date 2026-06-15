---
name: Edge Score card block & trade-strength classification
description: How the transparent Edge Score block + Possible/Strong trade-strength label on READY cards are computed; the floor-on-READY-verdict rule, the no-fabricated-signal rule, and the alert-driven nature behind the Liquidity Sweep reason.
---

# Edge Score block + trade-strength (display-only)

The clean READY trade card shows a transparent "⚡ Edge Score" block that REPLACES the
old free-text "🤖 AI Analysis" field, plus a 🟡 POSSIBLE TRADE / 🟢 STRONG TRADE label.
Both are a **display layer only**: computed from data `full_analysis()` already produces,
never requiring new webhook fields, and never altering webhook parsing, the READY/WAIT
gate, alerts, throttling, or the 5-min repost loop.

- Computed at the `_build_card_entry` seam (`compute_edge_breakdown` stamps
  `entry["edge_breakdown"]` and sets the authoritative `entry["edge_score"]`,
  `edge_grade`, `score_breakdown`, `risk_adjustments`, `trade_strength`). Journal +
  live card + periodic repost share one source. `_build_trade_card_embed` renders the
  block when present and FALLS BACK to the old AI Analysis field / `strict_label` when
  absent (legacy/manual entries).
- Score = gate base + bonuses − risks, then floored/clamped to 75–100 on READY.
  Gate base (the 4 required conditions): BOS +25, CHOCH +25, Confirmation +15,
  VWAP +10 = 75. Bonuses (additive): Sweep +8 OR Confirmed-Zone-Reaction +5 (mutually
  exclusive), Zone Active +5, Trend +4, Zone Mitigated +3, High/Elevated Confidence
  +6/+3. Risks SUBTRACT: Nearby Resistance/Support −4, Overextended −3, Choppy −3.
- Trade strength is a pure sub-classification of the Edge Score: Possible = 75–89,
  Strong = 90–100. The gate still decides READY/WAIT; strength only ranks a READY trade.

## The three durable rules

**Floor off the actual READY verdict, NOT a re-derived gate.** `compute_edge_breakdown`
runs ONLY on READY setups, so any READY trade must be floored to 75 (→ always 75–100,
always classifiable). Key the floor on the real verdict (`strict_label` in
Strong/Possible, or `verdict == READY`), not on re-deriving `has_bos and has_choch and
has_confirm and vwap_ok`.
**Why:** the gate has a zone-mitigation path that can pass WITHOUT a fresh BOS (see
`zone-mitigated-detection.md`). A re-derived 4-condition floor would leave a
mitigation-READY trade scoring <75 → `_trade_strength_from_score` returns None → the
label silently falls back to `strict_label`, breaking "once READY, classify by Edge
Score." Caught in review on this exact path.
**How to apply:** any future floor/gate-equivalent check inside a READY-only display
path should read the stored verdict, not recompute the gate conditions.

**Never fabricate a signal label.** A reason label must map to something the app actually
produces. The zone/sweep bonus shows "Liquidity Sweep" ONLY when a real sweep flag is set
(`confluences.liquidity_sweep` / `confluences.sweep` / `a["liquidity_sweep"]`); otherwise
it shows "Confirmed Zone Reaction" from `zone_confirmed`. The two are mutually exclusive.
An earlier version mislabeled `zone_confirmed` itself as "Liquidity Sweep" and was rejected.

**This app is alert-driven — it has NO OHLC/candle history.** All structure (BOS, CHOCH,
zone confirmed, confirmations, liquidity sweeps) arrives as TradingView webhook alerts in
`ALERT_TYPES` and is aggregated; nothing is computed from bars. A real "Liquidity Sweep"
cannot be detected from price action internally — it is wired as new alert types:
`{MGC,MNQ} {BULLISH,BEARISH} SWEEP`, each `side="sweep"`, `score=0`. `side="sweep"`/score 0
keeps them OUT of bias scoring and OUT of supply/demand level building;
`evaluate_strict_setup` detects them via the `_has()` recency helper and sets
`confluences.liquidity_sweep` on the READY long/short path.
**How to apply:** any future price-action / "edge" signal the chart sees but the server
cannot compute (sweeps, FVGs, displacement, etc.) must be added as a new alert type the
same way — not faked from a loosely-related existing flag.
