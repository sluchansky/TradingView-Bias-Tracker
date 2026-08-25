---
name: Native structure confirmed-pivot breaks
description: How the Databento native BOS/CHOCH detector must distinguish pivot confirmation from later level breaks.
---

Native BOS/CHOCH may only be emitted when a later close breaks a *previously confirmed* swing high or low. Do not test a break against the pivot being confirmed in the same evaluation window.

**Why:** The current bar is part of a pivot's right-side confirmation window. A swing high must be at least as high as that bar, so the same bar cannot also close above it; the symmetric low condition is equally impossible. Comparing them silently prevents native break events.

**How to apply:** Preserve the full confirmation window and sequence/dedupe semantics, then evaluate every new closed bar against the durable last-confirmed high/low. Regression coverage should prove both a continuation BOS and a later opposite-side CHOCH.