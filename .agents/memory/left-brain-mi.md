---
name: Left Brain Market Intelligence (Phase 1B)
description: Flag-gated DISPLAY-ONLY deterministic market-state classification at every 1m bar close.
---

## Architecture
- **Module:** `left_brain_market_intelligence.py` (new standalone file)
- **Feature flag:** `LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED` (env, default "0" → False)
- **Computation:** runs in `_databento_bar_scan` daemon thread AFTER `full_analysis` and `_right_brain_eval`; stored in `_LEFT_BRAIN_MI_BY_INST[inst]` (dict in app.py)
- **Consumption:** `full_analysis` reads `_LEFT_BRAIN_MI_BY_INST.get(inst)` and attaches at `result["left_brain"]["market_intelligence"]`
- **Flag OFF:** key absent from result → goldens byte-identical ✓

## Schema (all keys always present)
`available`, `instrument`, `computed_at`, `market_state` (11 states), `session_character` (9 types), `session_phase` (7 phases), `auction_control` (BUYER/SELLER/CONTESTED), `directional_outlook` ({long, short, neutral} summing to 100), `data_confidence` (0-100 freshness-based NOT agreement), `suitable_playbooks` (≤8 families), `supporting_evidence`, `missing_evidence`, `what_changes_thesis`, `narrative`

## Critical invariants
- `directional_outlook.long + short + neutral == 100` ALWAYS (rounding absorbed by neutral)
- Evidence families (VWAP 20 + Structure 25 + CVD 20 + Vol 15 + Session 10 + PriceAction 10 = 100) — total guaranteed by design
- FAIL-OPEN: `compute_left_brain_mi` never raises; returns neutral block on any exception
- Never recomputes inline in `full_analysis` (kept out of the request thread)

## Tests
31 tests in `test_left_brain_mi.py` covering all three parts: VWAP-01..07, DEDUP-01..04, MI-01..20.

**Why:** Computing MI inside `full_analysis` would add latency to every /status request. Computed once at bar-close, cached, then read in O(1).

**How to apply:** When adding new MI fields, update the neutral block in `_neutral_block()` too (stable schema contract).
