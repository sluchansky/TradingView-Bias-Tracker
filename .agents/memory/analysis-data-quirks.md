---
name: full_analysis data quirks & card-extension seam
description: Non-obvious facts about full_analysis() output and how to extend the trade-card/journal safely.
---

# full_analysis() data quirks

- **`vwap_status` is FRESHNESS, not direction.** `get_vwap()` returns status `ok`/`missing`/`stale` (age check), and `full_analysis()` passes that straight through as `vwap_status`. To know whether price is **above/below** VWAP, compare `current_price` vs `vwap_value` yourself — do NOT read direction from `vwap_status`.
  **Why:** I initially assumed `vwap_status` held "above"/"below"; it doesn't. The above/below signal lives in the `vwap` confluence boolean and in the price-vs-value comparison.
  **How to apply:** any "Above/Below VWAP" display must derive from `float(current_price) >= float(vwap_value)`, guarded for non-numerics.

- **BOS/CHOCH levels live in `last_price_by_type`** keyed by the strings `"BOS DEMAND"`, `"BOS SUPPLY"`, `"CHOCH DEMAND"`, `"CHOCH SUPPLY"` (side-specific). Longs read the DEMAND keys, shorts the SUPPLY keys.

# Extending the trade card / journal

- **`_build_card_entry()` is the single source of truth** for BOTH the journal entry and the live/periodic alert card. Add any new display field there once and both surfaces get it for free. The periodic loop calls it with no webhook `record`, so anything derived from the payload (e.g. screenshot URL) must tolerate `record=None`.
- **Screenshot URLs are handed to Discord but NEVER fetched server-side** (SSRF-safe by design). Validation = http(s) scheme + reject private/loopback hosts + length cap; that's sufficient precisely because there is no server fetch.
- **Performance-analytics posts are terminal-only and deduped.** Fire only on Win/Loss/Breakeven (a Win/Loss with ~zero realized P&L is reclassified Breakeven); skip Pending/T1; dedupe per entry via `entry["analytics_posted"]` so re-evaluation never double-posts. JOURNAL is in-memory and resets on restart — stats are session-scoped (state this in the footer).
