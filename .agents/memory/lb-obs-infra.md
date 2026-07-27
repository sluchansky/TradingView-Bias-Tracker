---
name: Left Brain Obs-Infra Closure
description: Observation buffer, /lb-thesis-obs endpoint v2, playbook sort fix, OUTLOOK_SHIFT evidence, dashboard thesis panel — all implemented in the obs-infra closure task.
---

# Left Brain Phase 2 — Observation Infrastructure Closure

**Why:** The original Phase 2 launch (64c8a25) had several correctness gaps in the observation pipeline that needed closing before promoting from shadow mode.

## Changes made (commit 527f2c6)

### Part 1 — Observation retention
- `_LB_THESIS_OBS_BY_INST` maxlen: `120 → 5000`
- New field `top_playbook_fit_score` (was `top_playbook_fit`)
- New fields `vwap_age_ms` (ms since VWAP ts) and `mi_input_ts` (MI computed_at ISO str)
- Dedup guard: `_LB_THESIS_OBS_LAST_BAR[inst]` tracks last appended bar-ts at minute precision; same key → skip

### Part 2 — /lb-thesis-obs endpoint v2
- `?inst=X` → 400 if not in {MGC,MNQ,MES,MYM}
- `?limit=N` clamped 1–5000
- `?summary=1` omits observations list, returns distribution stats
- `retention` block: `max_observations_per_instrument=5000, estimated_minutes=5000`
- Per-instrument `oldest_ts` / `newest_ts` metadata
- Observations returned **newest-first** (reversed from deque order)

### Part 3 — Playbook ordering
- `_compute_playbook_reasoning()` now scores ALL playbooks, then `sort(fit_score DESC, name ASC)` before slicing top-3
- Root cause: was iterating `suitable_playbooks[:3]` before scoring → insertion order ≠ score order

### Part 4 — OUTLOOK_SHIFT evidence
- `_detect_significant_changes()` now builds guaranteed non-empty evidence for OUTLOOK_SHIFT
- Uses `mi.get("supporting_evidence")` if present; falls back to derived sentences from per-direction probability changes; ultimate fallback: "Dominant directional weight shifted from X% to Y%."
- Never copies from `prev_mi`

### Part 6 — Dashboard thesis panel
- Added `#lb-thesis-body` HTML div inside `#lb-side` (after `#lb-mi-body`)
- Added `renderLBMarketIntelligence()` (was called but never defined — was silently failing)
- Added `renderLBThesis()` with all 8 UI states; all dynamic text via `aiEsc()`
- Both called from `renderModules(d)` 

### Part 10 — Tests
- 24 new tests in `test_lb_phase2_obs.py` — all pass
- Full suite: 20 failures vs 21 pre-existing on baseline 3155301

## Production state (Part 5)
- Dev: confirmed at 527f2c6, Phase 2 fully operational
- Production: no `/lb-thesis` hits visible in deployment logs → likely behind `64c8a25`; **needs re-publish to get Phase 2 in prod**

## How to apply
- Any future change to the observation snapshot schema must add the new field to ALL 3 places: (1) the `_LB_THESIS_OBS_BY_INST[inst].append({...})` dict, (2) the `test_lb_phase2_obs.py` schema test, (3) the `/lb-thesis-obs` summary metrics if relevant
- The dedup key is `mi.get("computed_at", "")[:16]` (minute precision) — changing the MI computation frequency would require updating this
- `_LB_THESIS_OBS_LAST_BAR` is a separate module-level dict; it must be cleared/reset if the observation deque is cleared
