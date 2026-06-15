---
name: Unified Edge Score (single source of truth)
description: The transparent confluence Edge Score is the only user-visible score; legacy bias score is internal ranking-only; the hard-blocker must be instrument-scoped.
---

# Unified Edge Score

The transparent confluence-based score from `compute_edge_breakdown()` is the
**single source of truth** for the Edge Score across every user-facing surface:
`/status`, `/why`, the dashboard headline bar, Discord alert cards, the journal,
daily recaps, and weekly reports. `full_analysis` derives it once through
`_analysis_edge_breakdown(a)` and attaches `edge_score`/`edge_grade`/`edge_breakdown`
to its result; `_build_card_entry` reuses that same breakdown so card/journal can
never diverge from `/status`.

**Why:** the old bias-derived `calculate_edge_score()` and the transparent score
disagreed (e.g. 84 vs 76 on the same READY setup), so the number a user saw
depended on which surface rendered it. Unifying removes that contradiction.

**How to apply:**
- Legacy `calculate_edge_score()` survives ONLY as `legacy_edge_score`, used by
  `_edge_score_for_entry()` for **ranking fallback** on historical entries. It is
  **never displayed**. Display goes through `_display_edge_score(entry)` →
  transparent score or `"—"`; daily/weekly averages must filter to numeric-only.
- `compute_edge_breakdown` is **confluence-authoritative**: when `confluences`
  exist, BOS/CHOCH/VWAP credit comes from `conf.bos/choch/vwap`, not stale entry
  display-strings (those are a fallback for legacy/manual entries only).
- Any new user-facing surface that shows a score must read the unified
  `edge_score`/`edge_grade`, never `legacy_edge_score`.

## Hard blocker must be instrument-scoped

The blocker zeros the Edge Score (clears breakdown, single risk line, `score=0`)
when `a.zone_broken_active` or `a.zone_mitigated_near`.

**Why:** `ZONE_BROKEN_AT` is a single module global. Originally
`zone_broken_active = ZONE_BROKEN_AT is not None` was unscoped, so a broken **MGC**
zone forced **MNQ** to WAIT/Edge 0 (and vice versa) until expiry — a real
cross-instrument bug, not just test contamination. `_handle_zone_broken` now tags
`ZONE_BROKEN_AT["instrument"]`, and `full_analysis` gates
`zone_broken_active` by `instrument_of(active_ticker)` (untagged/legacy breaks
still apply globally as a safety fallback).

**How to apply:** any module-global trading state that can fire per-instrument must
be tagged with its instrument and gated by the analyzed `active_ticker`.
Price-keyed state (e.g. `MITIGATED_PRICES`) is naturally safe because MGC (~thousands)
and MNQ (~tens-of-thousands) price ranges are disjoint, so a proximity check never
cross-matches — but boolean/flag globals are NOT and must be scoped explicitly.
