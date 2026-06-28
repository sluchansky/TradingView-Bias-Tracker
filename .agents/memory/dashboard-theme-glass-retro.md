---
name: Dashboard glass redesign vs retro theme
description: New dashboard card/hero surfaces must add a retro-theme override or retro mode renders half-glass.
---

The dashboard (`/dashboard` inline HTML/CSS in `artifacts/tradingview-webhook/app.py`)
supports a retro terminal theme via `html[data-theme="retro"]` that overrides ONLY
the color CSS variables (--bg/--panel/--inset/--border/--amber/--cyan/--text/...).

The dark-glass redesign styles the cards and hero (#status-card, #rec-card, .mod,
#mod-brain) with HARD-CODED translucent `linear-gradient(...rgba...)` backgrounds and
pseudo-element glows (::before/::after) — NOT `var(--panel)`. Those literals bypass the
retro variable swap, so retro mode keeps showing blue-glass unless a paired
`html[data-theme="retro"]` rule resets the surface to var(--panel)/var(--inset) and
neutralizes the indigo glows.

**Why:** a two-stop glass gradient can't be expressed by a single `--panel` var, so the
design intentionally hard-codes it; that's the tradeoff that breaks retro fidelity.
(Architect flagged it in review.)

**How to apply:** any NEW dashboard surface that hard-codes a gradient/glow instead of
`var(--panel)`/`var(--inset)` MUST get a matching override inside the
`html[data-theme="retro"]` block, or retro renders inconsistently. Same pattern applies
to drop-indicator vs hover: `#view-live .mod.mod-drop-before/after` must out-rank
`#view-live .mod:hover` (equal specificity, so keep the drop rules later in source) or
drag feedback disappears under the hover box-shadow.
