---
name: Dashboard panel collapse + drag-reorder
description: Per-device display-only layer that lets the trader minimize and reorder dashboard panels; the convention new panels must follow to inherit it.
---

# Dashboard panel collapse + drag-reorder (display-only)

The Flask dashboard (`artifacts/tradingview-webhook/app.py`, inside the big HTML
string served by `/dashboard`) has a **purely front-end, per-device** layer that
lets the trader **minimize/expand** each panel and **drag-reorder** them. It is
persisted only in `localStorage` (`dashCollapsed` = {modId:1}, `dashOrder` =
{modId:index}) and **never touches the server / gate / scoring / money path**.
Restored via the `↕️ Reset layout` pill (clears both keys + reloads).

**Rule for adding a NEW dashboard panel:** wrap it as
`<div class="mod" id="mod-...">` whose FIRST child is `<div class="mod-h">…</div>`
(the title), AND give it a **stable `id`**. The init IIFE keys collapse/order on
`m.id`, so a panel with no id silently can't be collapsed or reordered, and order
won't persist for it.

**Why:** the controls are injected by progressive enhancement (a grip ⠿ + caret
are appended to every `#view-live .mod > .mod-h`), keyed by the panel's id. This
keeps the feature money-path-safe (goldens byte-identical) and zero-config for new
panels — but only if the panel follows the `.mod` > `.mod-h` + id convention.

**How to apply / gotchas:**
- Drag is gated to the SAME parent. Most panels are direct children of
  `#view-live`; `mod-scores` is nested in `#rec-card`, so it reorders only within
  its own group (intentional — it's part of the recommendation card).
- `applyOrder()` runs ONLY when a saved order exists, so the default layout is
  untouched for anyone who never reordered.
- `.mod-h` was made `display:flex`; the title text + any inline meta `<span>`
  become flex items (8px gap). Don't assume `.mod-h` is a plain block.
- Don't rebuild `.mod-h` via innerHTML in any render path — that would wipe the
  injected grip/caret. Existing render code only updates inner-id spans, so it's
  safe; keep it that way.

## Advanced-panels declutter gate (second display-only layer)

On top of collapse/reorder there is a **default-clean** gate: an `#adv-row`
toggle flips a `data-adv` attribute on `<html>` (persisted per-device in
`localStorage('dashAdv')`, default OFF). One CSS rule does the hiding:
`html:not([data-adv="1"]) #view-live .mod:not(#mod-real-results):not(#mod-brain):not(#mod-news):not(#mod-prop):not(.mb-hidden){display:none!important}`.

**GOTCHA — the core allowlist is a `:not()` chain, not a class.** When Advanced
is OFF, *every* `#view-live .mod` is hidden EXCEPT the four ids in that chain.
So a NEW panel you intend to be always-visible will silently vanish unless you
add its id to the `:not(...)` allowlist. A panel meant to live under "Advanced"
needs nothing — it's hidden by default automatically.
**Why:** chosen over tagging ~20 panels with a class (fewer edits, no FOUC —
the selector matches when `data-adv` is absent, so the clean default paints with
no flash). `mb-hidden` panels stay hidden regardless (Main-Brain consolidation
is authoritative). Simulated/advisory panels are hidden-by-default but RE-appear
when Advanced is ON — they are not permanently removed.
