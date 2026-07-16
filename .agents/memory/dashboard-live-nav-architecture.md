---
name: Unified Dashboard 5-section live-nav architecture
description: How the UNIFIED_DASHBOARD_ENABLED 5-section nav is wired; panel assignment map, HV session config location, flag-off byte-identity, parent-container trap, and the ln-hidden class approach.
---

# Unified Dashboard Live-Nav Architecture

## Rule
5-section nav (Overview/Brain/Analysis/Journal/Controls) is a **pure JS** show/hide — no `data-nav` attributes on individual panel divs. The section-to-ID mapping lives in `_liveNavSections` JS dict; `setLiveSection(sec)` iterates `_liveNavAllIds` and toggles the `ln-hidden` CSS class.

**Why:** ~50+ panel divs × individual edits is error-prone. A JS lookup map means the mapping is in one place, the panels are untouched, and adding a new panel to a section is a one-line JS change.

**How to apply:** When adding a new panel that should appear under a specific section, add its `id` string to the matching array in `_liveNavSections`. Don't add `data-nav` attributes to the HTML.

## CRITICAL: Use `ln-hidden` class, NOT `style.display`
`setLiveSection()` uses `el.classList.add/remove('ln-hidden')` where `.ln-hidden{display:none!important}`.

**Why this matters:** render functions like `renderAnalystMode(d)` set `style.display=''` on every 3-second poll. If we used `style.display='none'` for hiding, those render functions would override the section hiding. The `!important` class ensures the section switcher always wins.

**Do NOT revert to `_liveOrigDisplay` / `style.display` approach** — it was broken for exactly this reason.

## CRITICAL: Never hide parent containers
`setLiveSection()` must NEVER set `ln-hidden` on `#live-layout`, `#bl-bottom`, or `#bl-drawer-row`. Those containers hold child panels (`mod-chartprev`, `mod-mi`, `mod-scores`, `mod-assistant` are INSIDE `#bl-drawer-row`). Hiding the parent makes child show/hide a no-op.

## DOM structure (critical for panel assignment)
```
#live-layout (3-column verdict/brain — ALWAYS VISIBLE)
  #bl-left, #bl-center (mod-brain), #bl-right
#bl-bottom
  #bl-drawer-row  ← mod-chartprev, mod-mi, mod-assistant, mod-scores are INSIDE here
[after bl-bottom closes]
mod-ai-decision-center, mod-prob, mod-checklist, mod-mb-voice ... (all OUTSIDE, freely controlled)
```

## Current section mapping (in JS _liveNavSections, Phase 2)
- **overview**: mod-brain, mod-data-feed, mod-real-results, mod-hvsessions
- **brain**: **mod-ai-decision-center only** (consolidated single-panel view)
- **analysis**: chartprev, cvd, mi, scores, prob, fastentry, xmarket, scalpdiag, swingdiag, swingstrat, breakout, swing-v2, dual-sim, report + **moved brain detail panels**: checklist, microscalp, countdown, whynot, analyst, pro, entryq, debate, strategy, scalp-advisory, stalk-mode, active-thinking, mb-voice, mb-predictions, mb-narrative, mb-daytype
- **journal**: equity, trades, news, sessionq, trademgmt, learning, thesis, review, rule-engine + **moved**: memory, mb-events, mb-thesis, mb-learning
- **controls**: prop, training, bothold, atm, liverunner, autoexit, exec-reject, broker-send-log + **moved**: governor, mb-confidence, assistant

## AI Decision Center (Phase 2 — mod-ai-decision-center)
Single panel shown in Brain section. Contains:
- **Top summary**: big verdict text, bias/confidence/strategy grid, reason, entry quality, next trigger, invalidation
- **7 internal tabs**: Decision (debate/analyst/pro), Readiness (gate booleans), Strategy (engine), Entry Quality (score/components), Confidence (governor), Memory (trade memory), Micro Scalp
- Tab choice persisted in `localStorage('adc_tab')`; restored by `initUnifiedDash()`
- JS: `adcSetTab(tab)`, `renderAiDecisionCenter(d)`, `_adcEsc(s)`
- Called from `renderModules(d)` via `try{ renderAiDecisionCenter(d); }catch(e){}`
- DISPLAY-ONLY; never touches money path; old individual panels remain in DOM (now in Analysis section)

## Accessibility (Phase 1 additions)
- `#live-nav` has `role="tablist"` and `aria-label="Dashboard sections"`
- Each button has `role="tab"` and `aria-selected="true/false"` (toggled by `setLiveSection`)
- `_liveNavKeydown()` handles ArrowLeft/Right/Up/Down for keyboard nav
- `.ln-btn:focus-visible` has an amber outline

## Feature flags (near line 1779 in app.py)
- `UNIFIED_DASHBOARD_ENABLED` (default "1") — shows the 5-tab live-nav
- `HIGH_VOLUME_PANEL_ENABLED` (default "1") — enables `#mod-hvsessions` panel + `session_windows` in `/status`

## Template variable replacements (in `dashboard()` function)
```python
html = html.replace("__UNIFIED_DASH__", "1" if UNIFIED_DASHBOARD_ENABLED else "0")
```
JS reads: `var UNIFIED_DASH = ('__UNIFIED_DASH__' === '1');`

## High-Volume Sessions
- Config: `HIGH_VOLUME_SESSIONS` dict (near line 3856), 3 windows per instrument
- Compute function: `compute_hv_session_windows(ticker, now=None)` — pure clock function, fail-open, DISPLAY-ONLY
- Status key: `"session_windows"` added to `_build_status_payload()` return dict (None when flag off)

## Flag-off behavior
- `UNIFIED_DASH = false` → `setLiveSection()` is a no-op → all panels visible normally → byte-identical to today
- `HIGH_VOLUME_PANEL_ENABLED = false` → `session_windows: null` in `/status` → hvsUpdateFromStatus is a no-op
