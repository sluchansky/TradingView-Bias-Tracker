---
name: Main Brain UI redesign
description: Apple×OpenAI visual redesign of #mod-brain — structure, CSS classes, and JS wiring
---

## Design
- `#mod-brain` has `data-brain-state` attr (wait/ready/trade/closed) set by `mbSetAvatarFace(state)` insertion
- `.brain-hero`: ambient `.brain-orb-halo` + enlarged `mb-orb` (224×284px) + `.brain-state-pill` (id=mb-av-state) + `.brain-caption` (id=mb-caption) + `.brain-ctx-row`
- `.brain-intel`: 2×2 CSS grid — mb-market / mb-strategy / mb-risk / mb-tm (all old IDs kept, just new wrapper classes)
- Feed: `.brain-feed-wrap` > `.brain-section-lbl` + `#mb-feed`
- Secondary: `.brain-details-btn` toggle (onclick JS) → `#brain-details` (mb-judge, mb-stats, mb-mission-wrap, mb-cases, mb-liq)
- Chat: `.brain-chat-section` wraps session bar + chat log + input; old `mb-chat-h` suppressed via `#mod-brain .mb-chat-h{display:none}`
- `mb-av` preserved as hidden empty div (JS compat)

## Invariants
- Every existing `getElementById` target (mb-av-state, mb-caption, mb-av-ctx, mb-feed, etc.) still in DOM — just moved to new wrappers
- Old `.mod-h` drag handle removed — brain panel no longer collapse/drag-able (intentional)
- Goldens: byte-identical (no webhook logic touched)
- CSS all in `#mod-brain`-scoped selectors so it can't bleed to other panels
- Retro theme: paired `html[data-theme=retro] #mod-brain *` overrides at bottom of new CSS block

**Why:** "AI as product" — avatar center, everything secondary. Scores/gates collapse behind Details toggle.
**How to apply:** If adding a new always-visible element to brain panel, place it inside `.brain-hero` or after `.brain-intel` but before `.brain-details-btn`. If it belongs to the secondary layer, put it inside `#brain-details`.
