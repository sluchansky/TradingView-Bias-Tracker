---
name: Per-instrument dashboard view (MGC/MNQ tab switch)
description: How the dashboard's symbol tabs switch the displayed analysis, and the invariants any per-instrument change must preserve.
---

# Per-instrument dashboard view

The dashboard's MGC/MNQ tabs switch the *displayed* analysis, not just the manual-entry target. The tab's `sym` is sent as `/status?ticker=<sym>`, and `full_analysis(ticker_override=...)` resolves `active_ticker` from the override (falling back to `_active_ticker()` = last-alerted instrument when absent).

**Why:** the analysis model is built around a single "active" (last-alerted) instrument; without an override the user could only ever see whichever instrument alerted last, so tapping the other tab appeared to do nothing.

**How to apply — invariants when touching per-instrument display:**
- `full_analysis` resolves `active_ticker` once at the top; everything instrument-specific (VWAP, strict setup, price, price-context) must key off that one variable, not call `_active_ticker()` again.
- Price is strictly per-instrument: `CURRENT_PRICE_BY_TICKER` (alert-driven, keyed by `instrument_of(ticker or normalized)`) via `current_price_for()`. Never fall back across instruments — an MNQ view must never compare an MGC price to MNQ VWAP. Reset it in `/clear` in lockstep with `CURRENT_PRICE`.
- `get_price_context(inst)` must be passed `active_ticker` so nearest levels, market structure, and trade-plan anchors stay instrument-coherent. Filtering rule: instrument-prefixed zone types use the prefix; BOS/CHOCH use the record ticker; unprefixed+untickered alerts are shared (counted for either) — mirrors `evaluate_strict_setup`'s `_has`.
- Only the strict path is user-visible on the dashboard (verdict, strict_score, confluences checklist, instrument/price/VWAP meta, trade_plan). bias/strength/confidence/edge_score/setup_stage/market_direction/trade_opportunity are NOT rendered, so they are intentionally left unfiltered. If any of those become visible, filter scoring/window summaries by instrument too.
- `full_analysis` has two return dicts (main + zone-mitigated early return) that must keep identical top-level keys (see full-analysis-return-parity).
