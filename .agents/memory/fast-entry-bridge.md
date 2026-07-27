---
name: Fast-entry structure bridge
description: Why MICRO_CHOCH and SWEEP_RECLAIM now feed structure_confirmed
---

# Rule
MICRO_CHOCH_SHORT/LONG and SWEEP_RECLAIM_SHORT/LONG are "fast" side types that hit
`if normalized in FAST_ENTRY_TYPES: return early` in the webhook handler, BEFORE
`ALERT_HISTORY.append()`. They were never stored in ALERT_HISTORY.

The gate's `structure_confirmed` reads exclusively from ALERT_HISTORY via `_has()`.
So these real TradingView structure signals were silently invisible to the gate.

**Fix**: Bridge injected before the FAST_ENTRY_TYPES early return:
- `MICRO_CHOCH_SHORT` → `"CHOCH SUPPLY"` in ALERT_HISTORY
- `MICRO_CHOCH_LONG`  → `"CHOCH DEMAND"` in ALERT_HISTORY
- `SWEEP_RECLAIM_SHORT` → `"LH"` in ALERT_HISTORY
- `SWEEP_RECLAIM_LONG`  → `"HL"` in ALERT_HISTORY

DELTA_FLIP and MICRO_VWAP are NOT bridged (order-flow only, not price structure).

**Why:** The user's Pine scripts send MICRO_CHOCH/SWEEP_RECLAIM as their primary
structure signals. Without the bridge, structure_confirmed was always False when
these were the only signals → gate permanently at WAIT.

**How to apply:** 5-minute dedup via `_FE_BRIDGE_LAST[(inst, bridge_type)]`.
Any new "fast" side type that IS a structure signal must be added to the
bridge mapping (same pattern). Types that are NOT structure (DELTA_FLIP,
MICRO_VWAP) must NOT be bridged — they're display/diagnostics only.
