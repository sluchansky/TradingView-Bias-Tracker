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
- Score = additive sum of six confluence components, max 100, computed by the SHARED
  helper `compute_trade_edge_components(signals, vol_adj=0)`: Zone-valid +25, VWAP +20,
  Structure +20, Liquidity Sweep +15, Confirmation Candle +10, Session +10. NO gate base,
  NO 75-floor. The SAME helper backs the READY gate, so the displayed Edge Score == the gate
  score always. The ONLY non-confluence term is the **SCALP volatility modifier** `vol_adj`
  (Normal +10 / Elevated 0 / Extreme −10; 0 in SWING — see volatility-monitor-gate): it is
  appended as a "Volatility" breakdown line and the final score is clamped 0–100. No other
  subtraction exists.
- **Risk lines are INFORMATIONAL warnings only (`points: None`); they do NOT subtract.** Nearby
  Resistance/Support, Overextended, Choppy render as flags but never lower the score. The one
  exception is volatility in SCALP, which is a real scored modifier (`vol_adj`, above) AND also
  shows an informational regime risk line; in SWING volatility is a hard gate, not a score term.
  (The older model subtracted all risks and floored READY to 75 — both removed.)
- Trade strength sub-classifies the Edge Score: Possible = 80–89, Strong = 90–100 (a READY is
  always ≥80 by construction). The gate decides READY/WAIT; strength only ranks a READY trade.
- **Session Bonus +10 is a pure additive component, NOT READY-gated.** A preferred ET window
  (`get_session_state`: half-open `[05:00,08:00)`, `[08:00,11:00)`, `[20:00,23:00)`) adds +10 to
  the Edge Score directly. `full_analysis` computes the session state ONCE and threads the same
  instant into both the gate and the display so the +10 is identical on /status, /why, card, journal.

## Letter-grade bands (display-only, quality NOT verdict)

`_grade_for_score`: **95–100 A+ · 90–94 A · 85–89 B · 80–84 C · below 80 WAIT**. The grade
is a quality label only; it does NOT change the READY/WAIT verdict or journaling. Now that the
READY gate itself requires Edge ≥ 80, a READY setup is ALWAYS grade C or better — the old
"thin READY floored to 75 displays WAIT" tension is gone; anything < 80 is a genuine WAIT.
Note: `QUALITY_LABELS` (A+/A/B/C/D market-quality) is a DIFFERENT scale — do not conflate.

## The durable rules

**Gate score and display score MUST come from the same helper — never recompute points in two
places.** `compute_trade_edge_components(signals)` is the single source: the gate calls it to
decide READY, and `compute_edge_breakdown` calls it to display, reading the gate's `confluences`
(where `zone_mitigated` carries the full zone-VALID signal) so the two always agree.
**Why:** the previous design scored the gate and the card separately (gate base + 75-floor on one
side, additive bonuses/subtractive risks on the other) and they drifted. The only legitimate
divergence now is a hard blocker (`zone_broken_active`/`zone_mitigated_near`) zeroing the DISPLAY
score while `gate_debug.edge_score` still shows raw components.
**How to apply:** any new edge component goes in `EDGE_COMPONENTS` ONCE; never add a credit in the
display layer that the gate doesn't also see, or READY and the shown score will disagree again.

**The READY threshold must not exceed the sum of the HARD-required components.** The three
required gates (zone 25 + vwap 20 + structure 20 = 65) are AND-ed, but `EDGE_READY_THRESHOLD = 80`,
so passing all three is NOT enough — READY structurally always needs a 4th confluence (sweep +15,
or candle +10 plus session +10). And since `zone_valid` itself requires a reaction, a clean
mitigation+candle+structure+VWAP setup OUTSIDE a session window tops out at 75 → still WAIT.
**Why:** this is a silent over-filter; if "always WAIT" recurs with the three requireds green, the
80 > 65 gap is the cause. **How to apply:** fix by lowering the threshold to 65 (three requireds =
READY, extras = Strong), or reweighting the three requireds to total 80 — do not just keep raising
inputs. Keep the threshold ≤ the AND-required sum unless a 4th confluence is intentionally required.

**Never fabricate a signal label.** A reason label must map to something the app actually
produces. The bonus shows "Liquidity Sweep" ONLY when a real sweep flag is set
(`confluences.liquidity_sweep` / `confluences.sweep` / `a["liquidity_sweep"]`), and shows
"Confirmed Zone Reaction" from `zone_confirmed`. These are INDEPENDENT — both can appear on
one card — NOT mutually exclusive. An earlier version mislabeled `zone_confirmed` itself as
"Liquidity Sweep" and was rejected.

**Reaction-satisfied (`confirmation`) ≠ real 5m candle (`confirmation_candle`).** The READY
gate's reaction requirement is met by a genuine 5m confirmation candle OR — on the mitigation
path — by a DEMAND/SUPPLY ZONE CONFIRMED alert. `_confluences()` emits BOTH `confirmation`
(reaction satisfied; drives the gate) and `confirmation_candle` (a real candle only; defaults
to `confirmation` for legacy dicts). The Edge Score credits "Confirmation Candle" +10 ONLY from
`confirmation_candle`; a zone-confirmed reaction has NO standalone credit — it feeds `zone_valid`
(+25) as the reaction half of mitigation+reaction, counted ONCE there and never as a phantom
candle; READY + Edge Score + the strict checklist all name the same event.
**Why:** the mitigation path once set `confirmation = has_bull_confirm OR mitigation_long_confirmed`,
so a zone-confirmed READY scored a phantom +15 candle AND suppressed the real zone-reaction
credit (and `/why` even told the user to "Add confluence: Confirmed Zone Reaction" while it
was present). **How to apply:** keep gate-satisfaction flags separate from "what literally
fired" flags whenever one requirement can be met by multiple distinct events; credit each once.

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
