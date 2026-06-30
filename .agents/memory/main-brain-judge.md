---
name: Main Brain Judge panel
description: Display-only consolidated-verdict dashboard panel — invariants that keep it from misleading on a live-money app.
---

The Main Brain Judge is a DISPLAY-ONLY dashboard panel that consolidates the
already-computed analysis into one explained verdict view (final label, decision
hierarchy, weighted Edge breakdown, missing confirmations). It lives at the same
full_analysis seam as the rest of the Main Brain cognitive family and must never
touch the gate / scoring / sizing / dedupe / broker money path.

**Rule: it MIRRORS the authoritative verdict, never computes a new one.** The final
label is derived from the bot's own `verdict`/`is_actionable` + direction, plus
position/market state. It must never apply its own thresholds that could disagree
with what the bot actually does.

**Rule: reuse the SAME edge helper that produced `result["edge_score"]`.** The score
block is built from `_analysis_edge_breakdown(result)` (read-only, no mutation) so the
Judge's number can never diverge from the headline Edge Score on the rest of the
dashboard. Don't recompute it a second, independent way.

**Final-label precedence (order matters):** open position (MANAGE OPEN TRADE) >
market closed (SKIP) > live READY / volatility-SKIP / WAIT.
**Why:** position detection must be resolved BEFORE the closed-market branch.
If the closed-market case returns early first, a live/manual position open during a
weekend/daily-halt is hidden behind SKIP instead of showing MANAGE — dangerous on a
live-money app where management status must stay visible while the market is paused.

**Rule: any state meant to RENDER must return `available=True`.** The frontend hides
the whole panel when `available` is false. `available=False` is reserved ONLY for a
genuine fail-open error (so a broken compute hides quietly). A real state like
closed-market SKIP must return the full 4-section schema with `available=True`, or the
SKIP label silently never appears.
**How to apply:** the closed-market path forces score 0 with every confluence absent
(mirrors the paused tape where `edge_score` is 0) but still returns the full available
schema.

**Testing:** guarded by its own smoke (not the goldens — it's additive/display-only, so
the scalp/swing/swing-flagoff goldens stay byte-identical). The smoke must cover the
closed-market render semantics and the position-outranks-closed precedence, not just
the open-market schema, because those were the two real bugs the first build shipped.
