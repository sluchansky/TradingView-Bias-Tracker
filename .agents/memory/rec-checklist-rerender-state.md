---
name: rec-checklist re-render wipes interactive state
description: Any user-toggled UI placed inside #rec-checklist is destroyed every poll; persist its state externally or it snaps back.
---

# #rec-checklist is fully rebuilt every poll — interactive children lose state

`renderDirView()` rebuilds `#rec-checklist` with `list.innerHTML = ...` on every
dashboard refresh (the 1s `setInterval` + the 3s `refreshRec`). Anything appended
inside it (e.g. the `🧮 Edge components` `<details class="edge-bd">`) is recreated
from scratch each tick, so any user interaction state (open/closed, scroll, input)
is **silently discarded** a second after the user sets it.

**Why:** the panel is not a `.mod`, so the dashboard's `.mod` collapse system
(`dashCollapsed` / `mod-min`, which enhances `#view-live .mod > .mod-h`) never
covers it. There is no shared persistence for children of `#rec-checklist`.

**How to apply:** when you add any user-toggled element inside `#rec-checklist`,
persist its state yourself (localStorage, per-device, display-only) and re-apply it
on each render — do NOT rely on the DOM keeping it. Pattern used for edge-bd:
`_edgeBdPref()` reads `localStorage 'edgeBdCollapsed'`, `onEdgeBdToggle(el)` writes
it, and the render computes the initial `open` from the pref (falling back to the
old default only when unset). Same clobber risk exists for any poll-rebuilt region
(cf. Today's-Trades pair-pin: the main poll renderer must early-return when pinned).
