---
name: Central Ghost Coordinator Phase 2B
description: Runtime safety decision for the live shadow-observation window.
---

The Central Ghost Coordinator operates as an observation-only intake during the Phase 2B live window. Fan-out/consolidation is disabled, while its own paired shadow evidence is durably persisted so safe restarts do not erase the comparison window. Existing source ledgers and their outcome resolvers remain authoritative.

**Why:** The purpose of Phase 2B is to collect paired live evidence without changing research ownership, execution safety, or historical data behavior.

**How to apply:** Keep coordinator intake and persistence enabled, with fan-out disabled. Do not redirect legacy writers, replace outcome resolvers, or use coordinator results in any gate, score, sizing, arming, broker, or execution decision until paired observations have been reviewed. An intake-only coordinator reconfiguration must preserve an installed persistence callback, because this app can be imported more than once at runtime.