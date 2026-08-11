---
name: Yahoo VWAP midnight UTC divergence pattern
description: Yahoo Finance VWAP produces two known anomaly episodes per session that cause temporary large divergences vs Databento.
---

## Pattern

Two distinct episodes per CME session produce outlier tick differences in the Yahoo↔Databento VWAP comparison:

### Episode 1 — Session-open warm-up (~17 minutes)
- **When:** 22:00–22:17 UTC (18:00–18:17 ET, EDT). First 10-17 comparison samples of each session.
- **Effect:** Yahoo VWAP starts 10–14 ticks ABOVE the fresh Databento accumulator for MGC. Much smaller (≤7 ticks) for MNQ, negligible for MES/MYM.
- **Root cause:** Yahoo Finance VWAP doesn't cleanly reset to the 18:00 ET session boundary. It carries residual weighting from the prior session, pushing it above the correct price until enough new-session bars dilute the old weight (~17 min for MGC's thin overnight volume).
- **Impact on promotion:** Breaks the streak for every new session. MGC's session open warm-up produces ~11 unacceptable samples per session, preventing the 50-consecutive-bar streak required for VALIDATING.

### Episode 2 — Calendar midnight UTC (00:00–00:01 UTC)
- **When:** Exactly at UTC calendar midnight, lasting 2 bars.
- **Effect:** Legacy VWAP jumps anomalously. MGC: +60.5 ticks (legacy=4429.53, databento=4423.48). MNQ: -103 ticks (legacy=29700.42, databento=29726.24 — Yahoo briefly reverts to prior settlement). Back to normal by 00:03.
- **Root cause:** Yahoo Finance recalculates "today's VWAP" at UTC midnight before accumulating enough intraday bars to anchor it. For MGC (thin overnight volume) the effect is extreme. MNQ direction inverts (Yahoo anchors to prior close, which was lower).
- **Impact on promotion:** Breaks any streak that crosses midnight UTC. For a 22:00–22:00 ET session, midnight UTC falls at the 2-hour mark. Any instrument with a clean session open will see its streak break here.

## Implication for promotion eligibility

Within a single session, an instrument qualifies for VALIDATING only if:
- Session open doesn't produce extended divergence (MNQ/MES/MYM: converge within 1-3 samples)
- Midnight UTC anomaly doesn't break a ≥50 consecutive streak (possible if ≥50 clean samples accumulate between session open convergence and midnight)

**MGC specifically cannot qualify** in a typical overnight session because:
1. Session open divergence consumes ~11 bars (22:01–22:17 UTC) → longest pre-midnight streak ≈ 40 bars
2. Even if session open were instant, the midnight UTC anomaly breaks the streak again 2 hours in

**MNQ/MES/MYM** can qualify if the session open settles within the first 1-3 bars and they accumulate 50+ bars before midnight UTC.

## What Databento VWAP is correct
The Databento VWAP is mathematically correct based on canonical 1m bar weighted volume from the 18:00 ET session start. Yahoo's anomalies are Yahoo Finance data artifacts — do NOT adjust the Databento calculation to match Yahoo during these episodes.

## When to expect resolution
Yahoo episodes are systemic; they will appear in every session at these two timestamps. VALIDATING promotion must be earned within a session's clean window (22:18 ET onward, prior to midnight UTC), or across multiple sessions once cross-session persistence is added.
