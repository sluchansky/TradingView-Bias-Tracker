---
name: Central Ghost Coordinator Phase 2B
description: Runtime safety decision for the live shadow-observation window.
---

The Central Ghost Coordinator is operating as an observation-only intake during the Phase 2B live window. Its fan-out/consolidation and durable coordinator persistence are intentionally disabled. Existing source ledgers and their outcome resolvers remain authoritative.

**Why:** The purpose of Phase 2B is to collect paired live evidence without changing research ownership, execution safety, or historical data behavior.

**How to apply:** Do not enable coordinator fan-out, redirect legacy writers, replace outcome resolvers, or use coordinator results in any gate, score, sizing, arming, broker, or execution decision until paired observations have been reviewed. A restart clears in-memory coordinator observations; use the existing persistent legacy ledgers as the durable comparison record for this window.