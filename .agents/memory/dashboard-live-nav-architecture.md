---
name: Unified Dashboard 5-section live-nav architecture
description: How the UNIFIED_DASHBOARD_ENABLED 5-section nav is wired; panel assignment map, HV session config location, flag-off byte-identity.
---

# Unified Dashboard Live-Nav Architecture

## Rule
5-section nav (Overview/Brain/Analysis/Journal/Controls) is a **pure JS** show/hide — no `data-nav` attributes on individual panel divs. The section-to-ID mapping lives in `_liveNavSections` JS dict; `setLiveSection(sec)` iterates `_liveNavAllIds` and toggles `el.style.display`.

**Why:** ~50 panel divs × individual edits is error-prone and hard to maintain. A JS lookup map means the mapping is in one place, the panels are untouched, and adding a new panel to a section is a one-line JS change.

**How to apply:** When adding a new panel that should appear under a specific section, add its `id` string to the matching array in `_liveNavSections`. Don't add `data-nav` attributes to the HTML — the JS approach is the chosen pattern.

## Feature flags (near line 1779 in app.py)
- `UNIFIED_DASHBOARD_ENABLED` (default "1") — shows the 5-tab live-nav
- `HIGH_VOLUME_PANEL_ENABLED` (default "1") — enables `#mod-hvsessions` panel + `session_windows` in `/status`
- `TOP_NAVIGATION_ENABLED` (default "1") — reserved

## Template variable replacements (in `dashboard()` function)
```python
html = html.replace("__UNIFIED_DASH__", "1" if UNIFIED_DASHBOARD_ENABLED else "0")
html = html.replace("__HV_PANEL__", "1" if HIGH_VOLUME_PANEL_ENABLED else "0")
```
JS reads: `var UNIFIED_DASH = ('__UNIFIED_DASH__' === '1');`

## High-Volume Sessions
- Config: `HIGH_VOLUME_SESSIONS` dict (near line 3856), 3 windows per instrument
  - MNQ/MES/MYM: 09:30, 10:30, 15:00 ET
  - MGC: 08:20, 09:20, 13:30 ET
- Compute function: `compute_hv_session_windows(ticker, now=None)` — pure clock function, fail-open, DISPLAY-ONLY
- Status key: `"session_windows"` added to `_build_status_payload()` return dict (None when flag off)
- Frontend render: `hvsUpdateFromStatus(d)` called inside `renderModules(d)` (injected after `renderActiveThinking`)
- Panel init: `initUnifiedDash()` called at page load after `setTimeout(pollThesis, 500)`

## Brain layout handling
`setLiveSection()` shows `#live-layout`, `#bl-bottom`, `#bl-drawer-row` ONLY in "overview" section; hides them in all other sections. This keeps the 3-column brain visible only on the Overview tab.

## Flag-off behavior
- `UNIFIED_DASH = false` → `setLiveSection()` is a no-op → all panels visible normally → byte-identical to today
- `HIGH_VOLUME_PANEL_ENABLED = false` → `session_windows: null` in `/status` → `hvsUpdateFromStatus` is a no-op → `#mod-hvsessions` still renders but shows "No session data"
