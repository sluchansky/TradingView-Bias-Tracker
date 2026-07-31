---
name: Left Brain thesis staleness & key mismatch
description: Root causes and fixes for MGC Left Brain thesis being blank/STALE in the Main Brain panel.
---

## Root Cause (proven from live /databento-status)

1. **MGC has ~1 Databento bar per overnight session** vs 127+ for MNQ/MES/MYM.
   - Databento GLBX.MDP3 delivers very few MGC.c.0 ticks overnight (3-6 AM ET on COMEX Micro Gold).
   - The thesis only updates on `_databento_bar_scan`, which only fires when a new-minute tick causes the previous bar to close.
   - MGC was 1h40min stale at 3:28 AM ET with 1 observation vs 127 for MNQ.

2. **Key-name mismatch**: `_mb_left_brain` read `raw_thesis.get("lastUpdatedAt")` (camelCase) but `compute_left_brain_thesis` stores `"last_updated_at"` (snake_case). This made `age_seconds` and `generated_at` always `None`, so staleness was invisible.

3. **No diagnostic state**: UI silently showed NEUTRAL (from stale computation) with no indication of staleness.

## Fix applied

- `_mb_left_brain` now reads `"last_updated_at"` first, then `"lastUpdatedAt"` as fallback.
- Added `diagnosis` block to `_mb_left_brain` return: `status` (AVAILABLE/STALE/COLLECTING_DATA/NO_DATA), `observation_count`, `databento_bars`, `thesis_age_seconds`, `blocked_reason`, `source_symbol`.
- `ThesisPanel` in MainBrain.tsx uses `diagnosis.status` for 4-state display; never shows NEUTRAL as a silent fallback.

**Why:** STALE threshold = 600s (10 min = 2× VWAP freshness window). MIN_OBS = 5 bar-close observations. STALE takes priority over COLLECTING_DATA.

## Architectural note

The Left Brain thesis for ALL instruments depends entirely on `_databento_bar_scan` (bar-close callback). A follow-up that adds a periodic timer fallback (every 5-10 min) to re-run `compute_left_brain_mi` + `compute_left_brain_thesis` for instruments with no recent bar-close would keep low-volume instruments from going STALE.

## Key files / functions

- `app.py` → `_mb_left_brain()` (adapter; contains the fix + diagnosis)
- `left_brain_market_intelligence.py` → `compute_left_brain_thesis()` (stores snake_case `last_updated_at`)
- `databento_brain.py` → `_databento_bar_scan()` (the only trigger for thesis updates)
- `artifacts/home/src/pages/MainBrain.tsx` → `ThesisPanel` (4-state UI)
- `artifacts/tradingview-webhook/test_lb_thesis_mgc.py` → 48 tests (Cases A–L)
