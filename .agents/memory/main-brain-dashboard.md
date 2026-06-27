---
name: Main Brain dashboard consolidation
description: How the trading dashboard's analyst-family panels were consolidated into ONE display-only "Main Brain" command center, and the non-obvious hiding/layout gotchas.
---

# Main Brain dashboard consolidation

The cluttered analyst-family dashboard boxes were folded into ONE display-only
"Main Brain" command center: a status badge + 4 brain-view lists
(Market/Strategy/Risk/Trade Manager) + a client-side scrolling plain-English
narration, all driven by a server-computed `result["main_brain"]` block.

**Rule: this whole feature is display-only.** It consumes the already-assembled
full_analysis blocks (analyst/debate/pro/entry-quality/volatility/edge/verdict),
never recomputes, never touches gate/sizing/dedupe/broker/traderspost. The
top-level key must be on the `/status` curated whitelist or it is `None` on the
wire. Goldens stay byte-identical.

## Hiding redundant panels — two traps

**Trap 1: render JS re-shows statically-hidden panels.** The 5 analyst panels are
written with inline `style="display:none"` BUT their render functions set
`mod.style.display=''` whenever their engine is enabled (i.e. during market
hours), so a static `display:none` is NOT enough. Hide them with a
`.mb-hidden{display:none !important}` CLASS instead — a stylesheet `!important`
declaration beats an inline non-`!important` `style.display` assignment, so the
render JS can stay intact and the panel still stays hidden.
**Why:** leaving render JS untouched keeps the change minimal/display-only; the
`!important` is what actually wins against the inline toggle.
**How to apply:** add `mb-hidden` to the container's class list; keep the render
function as-is. Would only fail if code later removed the class/rule or used
`style.setProperty('display','block','important')` (nothing does).

**Trap 2: `mod-report` id is DUPLICATED.** There are TWO `id="mod-report"`
panels — Unified Analyst Report (hide) and Performance Report (KEEP). An
`#mod-report` CSS/id selector would hit BOTH. Hiding via a CLASS on the specific
Unified Analyst Report container avoids clobbering Performance Report. Never use
an id selector to hide one of a duplicated id.

## Layout version reset

The per-device collapse/reorder layer (`dashCollapsed`/`dashOrder` in
localStorage) sorts any panel NOT in a saved `dashOrder` to the bottom (index
`1e9`). A newly-added panel (Main Brain) would therefore sink to the bottom for
existing users. Fix: a one-time reset keyed by a `dashLayoutVer` marker — if the
stored version != current `VER`, clear both keys once and write the new version,
so everyone falls back to default DOM order (Main Brain on top) exactly once;
later manual reorders persist again.
**How to apply:** when you add/remove a panel that should change default order,
bump `VER` deliberately.
