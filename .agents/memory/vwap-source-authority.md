---
name: VWAP source authority (Phase 1A fix)
description: Databento is the primary VWAP source; the old grace window was inverted; new dual-store + diagnostic approach.
---

## The bug
`databento_brain.py` had a 10-minute grace window that **blocked** Databento from writing `VWAP_BY_TICKER` whenever a TV/chart push arrived within 10 minutes. This was the wrong direction — Databento writes every minute and should always win when it has data. A chart push 9 min old would block all Databento VWAP for the remaining 1 min, leaving the gate with a potentially stale value.

## Fix applied
- Removed the grace window block from `_on_bar_close` in `databento_brain.py`. Databento now always writes `VWAP_BY_TICKER` unconditionally.
- Added `CHART_VWAP_BY_TICKER = {}` (secondary store, app.py) — TV webhook writes here always (additive, never replaced existing write to VWAP_BY_TICKER). Preserves TV supplement during Databento startup.
- TV webhook still writes `VWAP_BY_TICKER` too (backward compat), but Databento overwrites it within ~60 s.

## New API
`get_vwap_diagnostics(ticker)` → returns dict with:
- `vwap_source`, `vwap_age_ms`, `databento_vwap_available`, `databento_vwap_value`, `databento_vwap_age_ms`
- `chart_vwap_available`, `chart_vwap_value`, `chart_vwap_age_ms`
- `source_selection_reason`, `selection_correct` (self-audit)

`result["vwap_diagnostics"]` is **always present** in `full_analysis` output (not flag-gated).
Also whitelisted in `/status` endpoint.

**Why:** Databento provides a bar-close VWAP every minute from live tick accumulation; TV chart pushes are manual or on-demand. Databento is always fresher after the first bar.

**How to apply:** Any future VWAP source must write to `CHART_VWAP_BY_TICKER` if it's supplementary (not Databento), and never add a grace window that blocks Databento writes.
