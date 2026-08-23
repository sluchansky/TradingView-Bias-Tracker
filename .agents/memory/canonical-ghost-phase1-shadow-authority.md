---
name: Canonical Ghost Phase 1 shadow authority
description: Rules for the SCALP and INTRADAY canonical ghost reconciliation sidecar.
---

The generic `ghost_observations` lifecycle is the only canonical observation and
outcome authority in Phase 1. Other research systems can appear only as
comparison references after they present the exact same coordinator identity;
there is never time-, price-, or status-based fuzzy matching.

**Why:** A shadow reporting layer must not accidentally turn a simulator,
research engine, or missing record into a second authority or a false
disagreement.

**How to apply:** Keep the sidecar append-only and outside every money path.
On persistence failure, expose and retry the pending copy; after restart,
recover only by joining the durable coordinator record to the generic ledger
with the existing stable observation key. Do not replace legacy writers,
resolvers, learning, promotion, gates, or execution until a separately approved
cutover.