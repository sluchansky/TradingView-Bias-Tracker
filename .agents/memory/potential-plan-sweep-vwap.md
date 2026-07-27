---
name: Potential-plan sweep+VWAP gate
description: When and why potential_plan (the forming-setup dashboard preview) generates
---

# Rule
`potential_plan` (per-direction forming-setup entry/stop/TP preview) is generated when:
- `_gd.get("structure_confirmed")` — BOS/CHOCH/HH/HL in ALERT_HISTORY within 30 min, OR
- `_gd.get("liquidity_sweep") AND _gd.get("vwap_confirmed")` — sweep cleared opposing liquidity AND VWAP confirms direction

Previously was exclusively `structure_confirmed`.

**Why:** During low-volume / overnight sessions, structure alerts don't fire for 30+ min. The SCALP `build_strict_trade_plan` anchors on VWAP when no zone present, so sweep+VWAP always produces a valid entry/stop/TP. This ensures the dashboard always shows forming plans when Databento detects sweeps.

**How to apply:** `potential_plan` lives in `directions[dir]` inside `full_analysis`. It is display-only — broker/money path reads exclusively from the top-level `trade_plan`. Never gate broker actions on `potential_plan`. Goldens are byte-identical because goldens have no sweep+VWAP combination without structure_confirmed in their pinned ALERT_HISTORY state.
