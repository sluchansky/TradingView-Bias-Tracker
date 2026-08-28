---
name: Coordinator durable health totals
description: How bounded coordinator restores and complete research health should coexist.
---

Keep the coordinator's startup restore bounded to the newest active window so exact duplicate detection remains memory-local and affordable. Do not use that window as the health denominator: complete opportunity, observation, and heartbeat totals must come from read-only aggregates over the durable append-only tables. Expose durable-complete and restored-session scopes separately to operators.

**Why:** Long-lived deployments can retain more durable coordinator rows than a safe startup restore can load, so session-only counts silently under-report research coverage and heartbeats.

**How to apply:** Preserve the bounded restore and chronological replay order, attach a fail-open aggregate callback to coordinator reporting, and make every operator surface label which scope it is displaying.