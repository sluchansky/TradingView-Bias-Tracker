---
name: State-aware BOS/CHOCH cycle
description: The scoring and strict-gate contract for conventional market-structure event sequencing.
---

Treat BOS and CHOCH as ordered events within one active, per-instrument structure cycle. A valid CHOCH starts a 20-point reversal candidate. A neutral BOS starts `TREND_INITIAL` at 20 points only; the next same-direction BOS confirms `TREND_CONFIRMED` at 40. A same-direction BOS after CHOCH confirms `REVERSAL_CONFIRMED` at 40, and later aligned BOS events retain that one allocation. A newer opposite CHOCH supersedes the prior cycle. Ignore opposite BOS without its own CHOCH, same-direction CHOCH that is not counter-trend, and duplicate candidate input.

**Why:** Independent BOS/CHOCH booleans double-counted stale or conflicting evidence and let historical structure remain actionable after a fresh reversal signal. HH/HL/LH/LL are useful diagnostics but are not a substitute for an active confirmed cycle.

**How to apply:** Keep SCALP and INTRADAY_TREND on this shared resolver. Preserve raw event flags for diagnostics, but use the resolved active cycle for structure scoring, the strict structure gate, status payloads, and Main Brain presentation. Initial/candidate cycles may carry reduced score allocation but can never satisfy the strict structure gate. Maintain the 110-point ceiling and leave non-structure weights unchanged.