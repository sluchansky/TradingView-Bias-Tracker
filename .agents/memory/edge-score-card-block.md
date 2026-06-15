---
name: Edge Score card block
description: How the Edge Score / Grade / Reasons / Risk block on READY trade cards is computed, and the no-fabricated-signal rule plus the alert-driven nature behind the Liquidity Sweep reason.
---

# Edge Score block (display-only)

The clean READY trade card shows an "⚡ Edge Score" block (Score / Grade / Reasons / Risk)
that REPLACES the old free-text "🤖 AI Analysis" field. It is a **display layer only**:
computed internally from data `full_analysis()` already produces and never requires new
webhook payload fields, nor alters webhook parsing, throttling, or the 5-min repost loop.

- Computed at the `_build_card_entry` seam (stamps `entry["edge_breakdown"]`), so journal +
  live card + periodic repost share one source. `_build_trade_card_embed` renders the block
  when present and FALLS BACK to the old AI Analysis field when absent (legacy/manual entries).
- Score is a bounded weighted sum (0–100) over genuine confluences (BOS, CHOCH, VWAP
  position, zone active, trend, a zone/sweep slot) plus a confidence term; grade A+ at ≥90.

## The two durable rules

**Never fabricate a signal label.** A signal/reason label must map to something the app
actually produces. The zone/sweep slot shows "Liquidity Sweep" ONLY when a real sweep flag
is set (`confluences.liquidity_sweep` / `confluences.sweep` / `a["liquidity_sweep"]`);
otherwise it shows "Confirmed Zone Reaction" from `zone_confirmed`. The two are mutually
exclusive (one shared 12-pt slot — no double count). An earlier version mislabeled
`zone_confirmed` itself as "Liquidity Sweep" and was rejected in review.

**This app is alert-driven — it has NO OHLC/candle history.** All structure (BOS, CHOCH,
zone confirmed, confirmations, and now liquidity sweeps) arrives as TradingView webhook
alerts in `ALERT_TYPES` and is aggregated; nothing is computed from bars. So a real
"Liquidity Sweep" cannot be detected from price action internally — it is wired as new
alert types: `{MGC,MNQ} {BULLISH,BEARISH} SWEEP`, each `side="sweep"`, `score=0`.
`side="sweep"`/score 0 keeps them OUT of bias scoring (the loop only adds bullish/bearish)
and OUT of supply/demand level building (`get_price_context` excludes `SWEEP_TYPES`);
`evaluate_strict_setup` detects them via the existing `_has()` recency helper and sets
`confluences.liquidity_sweep` on the READY long/short path.

**How to apply:** any future price-action / "edge" signal that the chart sees but the
server cannot compute (sweeps, FVGs, displacement, etc.) must be added as a new alert type
the same way — not faked from a loosely-related existing flag.
