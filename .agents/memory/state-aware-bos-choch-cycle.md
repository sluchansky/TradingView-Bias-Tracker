---
name: State-aware BOS/CHOCH cycle
description: The scoring and strict-gate contract for conventional market-structure event sequencing.
---

Treat BOS and CHOCH as ordered events within one active, per-instrument structure cycle. A valid CHOCH starts a reversal candidate worth 20 structure points. A same-direction BOS confirms or continues that cycle at 40 points; no cycle can earn more. A newer opposite CHOCH supersedes the prior cycle. Ignore a BOS that lacks a preceding same-side CHOCH, and do not refresh a cycle with duplicate CHOCH input.

**Why:** Independent BOS/CHOCH booleans double-counted stale or conflicting evidence and let historical structure remain actionable after a fresh reversal signal. HH/HL/LH/LL are useful diagnostics but are not a substitute for an active confirmed cycle.

**How to apply:** Keep SCALP and INTRADAY_TREND on this shared resolver. Preserve raw event flags for diagnostics, but use the resolved active cycle for structure scoring, the strict structure gate, status payloads, and Main Brain presentation. Maintain the 110-point ceiling and leave non-structure weights unchanged.