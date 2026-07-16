---
name: Unified Dashboard 5-section live-nav architecture
description: How the UNIFIED_DASHBOARD_ENABLED 5-section nav is wired; panel assignment map, HV session config location, flag-off byte-identity, and the parent-container trap.
---

# Unified Dashboard Live-Nav Architecture

## Rule
5-section nav (Overview/Brain/Analysis/Journal/Controls) is a **pure JS** show/hide — no `data-nav` attributes on individual panel divs. The section-to-ID mapping lives in `_liveNavSections` JS dict; `setLiveSection(sec)` iterates `_liveNavAllIds` and toggles `el.style.display`.

**Why:** ~50 panel divs × individual edits is error-prone and hard to maintain. A JS lookup map means the mapping is in one place, the panels are untouched, and adding a new panel to a section is a one-line JS change.

**How to apply:** When adding a new panel that should appear under a specific section, add its `id` string to the matching array in `_liveNavSections`. Don't add `data-nav` attributes to the HTML — the JS approach is the chosen pattern.

## CRITICAL: Never hide parent containers
`setLiveSection()` must NEVER set `style.display='none'` on `#live-layout`, `#bl-bottom`, or `#bl-drawer-row`. Those containers hold child panels (`mod-chartprev`, `mod-mi`, `mod-scores`, `mod-assistant` are INSIDE `#bl-drawer-row`). Hiding the parent makes `el.style.display = ''` on children a no-op — the panels appear hidden even when the JS shows them. The verdict/brain layout stays visible in every section as the anchor.

## DOM structure (critical for panel assignment)
```
#live-layout (3-column verdict/brain — ALWAYS VISIBLE)
  #bl-left, #bl-center (mod-brain), #bl-right
#bl-bottom
  #bl-drawer-row  ← mod-chartprev, mod-mi, mod-assistant, mod-scores are INSIDE here
                    (individually controlled via display, but never hide the container)
[after bl-bottom closes]
mod-prob, mod-checklist, mod-mb-voice, mod-mb-predictions ... (all OUTSIDE, freely controlled)
```

## Current section mapping (in JS _liveNavSections)
- **overview**: mod-brain, mod-data-feed, mod-real-results, mod-hvsessions
- **brain**: checklist, microscalp, countdown, whynot, analyst, pro, entryq, debate, strategy, governor, memory, mb-voice/predictions/confidence/narrative/events/thesis/daytype/learning, assistant, scalp-advisory, stalk-mode, active-thinking
- **analysis**: chartprev, cvd, mi, scores, prob, fastentry, xmarket, scalpdiag, swingdiag, swingstrat, breakout, swing-v2, dual-sim, report
- **journal**: equity, trades, news, sessionq, trademgmt, learning, thesis, review, rule-engine
- **controls**: prop, training, bothold, atm, liverunner, autoexit, exec-reject, broker-send-log

## Accessibility (Phase 1)
- `#live-nav` has `role="tablist"` and `aria-label="Dashboard sections"`
- Each button has `role="tab"` and `aria-selected="true/false"` (toggled by `setLiveSection`)
- `_liveNavKeydown()` handles ArrowLeft/Right/Up/Down for keyboard nav
- `.ln-btn:focus-visible` has an amber outline

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
- Compute function: `compute_hv_session_windows(ticker, now=None)` — pure clock function, fail-open, DISPLAY-ONLY
- Status key: `"session_windows"` added to `_build_status_payload()` return dict (None when flag off)

## Flag-off behavior
- `UNIFIED_DASH = false` → `setLiveSection()` is a no-op → all panels visible normally → byte-identical to today
- `HIGH_VOLUME_PANEL_ENABLED = false` → `session_windows: null` in `/status` → hvsUpdateFromStatus is a no-op
