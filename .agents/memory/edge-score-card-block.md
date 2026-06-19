---
name: Edge Score card block & trade-strength classification
description: How the transparent Edge Score block + Possible/Strong/A+ tier label on READY cards are computed; the weighted-component model (max 110), the no-fabricated-signal rule, the gate==display invariant, and the alert-driven nature behind Sweep/Volume/CVD.
---

# Edge Score block + trade-strength (display-only)

The clean READY trade card shows a transparent "⚡ Edge Score" block plus a
🟡 POSSIBLE TRADE / 🟢 STRONG TRADE / A+ tier label. Both are a **display layer only**:
computed from data `full_analysis()` already produces, never altering webhook parsing,
the READY/WAIT gate, alerts, throttling, or the 5-min repost loop.

- Computed at the `_build_card_entry` seam; `_analysis_edge_breakdown` reuses
  `compute_edge_breakdown`, which calls the ONE scorer, so journal + live card +
  periodic repost + /status all share one source.

## The weighted-component model (current)

- Score = additive sum of weighted components, **max `EDGE_SCORE_MAX = 110`**, via the
  SHARED helper **`compute_trade_edge_components(signals)`** (single arg — the old
  `vol_adj`/`rvol_adj` modifier params were REMOVED). `signals` is a dict of booleans
  keyed by `EDGE_COMPONENTS[*][0]`.
- **`EDGE_COMPONENTS` (7 tuples, sum 110):** BOS Confirmed +20, CHOCH Confirmed +20,
  VWAP +15, Liquidity Sweep +15, **Volume +15** (a recent volume spike OR RVOL ≥
  `cfg("RVOL_CONFIRM_THRESHOLD")` ≈ 1.5), **CVD Agreement +15**, Session +10.
- **Structure is SPLIT into two independent components** (BOS +20 and CHOCH +20) — both
  can credit on one setup. There is NO combined "Structure +20" term anymore.
- **Dropped from SCORING vs the old model:** `zone_valid` (+25) and
  `confirmation_candle` (+10) no longer score at all; the separate RVOL ± and
  volatility ± modifiers are gone. Volatility is now informational + a SWING hard gate
  only; RVOL feeds the Volume component (spike OR RVOL≥thr), not a standalone modifier.
- `cap_applied` is effectively always False (raw max is exactly 110 = EDGE_SCORE_MAX);
  it stays in the diagnostics for honesty if weights ever exceed the cap.
- **Risk lines are INFORMATIONAL warnings only (`points: None`); they never subtract.**
  The only DISPLAY divergence from the gate score is a hard blocker
  (`zone_broken_active`/`zone_mitigated_near`) zeroing the display score while
  `gate_debug.edge_score` still reports raw components.

## Tiers and grades (display-only, quality NOT verdict)

- **Trade strength (`_trade_strength_from_score`): Possible ≥50, Strong ≥70, A+ Setup
  ≥85** (below 50 → None). "A+ Setup" feeds the DISPLAY `trade_strength` ONLY — it must
  NEVER become `strict_label` (journaling label stays in {Strong Trade, Possible Trade,
  WAIT}).
- **Grade (`_grade_for_score`): ≥85 A+ · ≥70 A · ≥50 B · below WAIT.** Quality label
  only; never changes the READY/WAIT verdict. A valid SCALP READY (Edge ≥70) is always
  grade A or better. `QUALITY_LABELS` (A+/A/B/C/D market-quality) is a DIFFERENT scale.
- **Session +10 is a pure additive component, NOT READY-gated.** `full_analysis`
  computes the session state ONCE and threads the same instant into gate + display so
  the +10 is identical everywhere.

## The durable rules

**Gate score and display score MUST come from the same helper — never recompute points
in two places.** `compute_trade_edge_components(signals)` is the single source: the gate
(`_signals`) calls it to decide READY; `compute_edge_breakdown` calls it to display.
**Why:** the previous design scored gate and card separately and they drifted.
**How to apply:** any new edge component goes in `EDGE_COMPONENTS` ONCE, with a matching
boolean key produced by BOTH `_signals` (gate) and `compute_edge_breakdown` (display);
never add a credit one side can't see, or READY and the shown score disagree again.

**Never fabricate a signal label.** A reason label must map to something the app
actually produces (a real sweep flag, real volume confirmation, real CVD agreement) —
see zone-presence-not-from-strings.md for the phantom-zone bug this prevents.

**This app is alert-driven — it has NO OHLC/candle history.** All structure (BOS, CHOCH,
zone confirmed, sweeps), plus Volume and CVD, arrive as TradingView webhook alerts in
`ALERT_TYPES` and are aggregated; nothing is computed from bars. Any new price-action /
edge signal the chart sees but the server cannot compute must be added as a NEW alert
type (e.g. `{MGC,MNQ} {BULLISH,BEARISH} SWEEP`, volume/CVD alerts) — never faked from a
loosely-related existing flag.
