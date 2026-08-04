---
name: ATR stop multiplier history
description: Progression of STOP_ATR_MULT values and the MAX_RISK_DOLLARS coupling that must change with them.
---

## Current values (SCALP + SWING legacy)
- `STOP_ATR_MULT` = **2.5** (normal vol)
- `STOP_ATR_MULT_HIGH` = **3.0** (HIGH_CAUTION / HIGH_BLOCK)
- `MAX_RISK_DOLLARS` (SCALP) = **$100**

SWING flag-on (`SWING_HTF_ENABLED`) keeps its own wider keys: `SWING_STOP_ATR_MULT` = 2.25, `SWING_STOP_ATR_MULT_HIGH` = 2.75 — unchanged.

## Progression
- 0.75 / 1.25 → **1.5 / 2.0** (widened because sub-ATR stops were tighter than instrument noise)
- 1.5 / 2.0 → **2.5 / 3.0** (still getting wicked out on all instruments in live trading)

## Critical coupling: MAX_RISK_DOLLARS must move with the multiplier
MNQ stop at 2.5×ATR ≈ 35.7 pts × $2 = **$71.40/contract**.  
If `MAX_RISK_DOLLARS` (SCALP) stays at $50, `_risk_capped_contracts` returns `over_cap=True` → **0 contracts → every MNQ auto-trade silently blocked**.  
Always recompute `ATR × mult × point_value` for MNQ before setting the dollar cap.

**Why:** `_risk_capped_contracts` hard-caps at `MAX_RISK_DOLLARS`; when a single contract exceeds it, `n_full < 1` → `over_cap=True`, caller skips.

**How to apply:** Any future multiplier change must recalculate MNQ dollar risk at the new mult and confirm `MAX_RISK_DOLLARS` ≥ that value.
