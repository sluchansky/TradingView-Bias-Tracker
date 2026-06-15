---
name: Edge Score card block
description: How the Edge Score / Grade / Reasons / Risk block on READY trade cards is computed and the no-fabricated-signal rule it must obey.
---

# Edge Score block (display-only)

The clean READY trade card shows an "⚡ Edge Score" block (Score / Grade / Reasons / Risk)
that REPLACES the old free-text "🤖 AI Analysis" field. It is a **display layer only**:
it is computed internally from data `full_analysis()` already produces and must never
require TradingView to send new fields, nor alter webhook parsing, throttling, or the
5-min repost loop.

- Computed at the `_build_card_entry` seam: it stamps `entry["edge_breakdown"]`, so journal
  + live card + periodic repost all share one source. `_build_trade_card_embed` renders the
  block when `edge_breakdown` is present and FALLS BACK to the old AI Analysis field when
  absent (legacy/manual journal entries).
- Score is a bounded weighted sum (0–100) over genuine confluences (BOS, CHOCH, VWAP
  position, zone active, trend, a zone/sweep slot) plus a confidence term; grade A+ at ≥90.

**Why (the durable rule): never fabricate a signal label.** The app has NO dedicated
liquidity-sweep detector. An earlier version labeled `confluences.zone_confirmed` as
"Liquidity Sweep" — that is a mislabel (zone confirmation ≠ a sweep) and was rejected in
review. The slot now reports the genuine signal: show "Liquidity Sweep" ONLY when a real
flag exists (`confluences.liquidity_sweep` / `confluences.sweep` / `a["liquidity_sweep"]`),
otherwise show "Confirmed Zone Reaction" from `zone_confirmed`. The two are mutually
exclusive (one shared 12-pt slot — no double count).

**How to apply:** any future "edge"/scoring/reason label must map to a signal the app
actually produces. If you want a true Liquidity Sweep reason, wire a real sweep
signal/alert first, then it lights up automatically via the existing flag check.
On READY cards BOS/CHOCH/VWAP are gate requirements (always ✓); zone/trend/sweep are the
variable reasons.
