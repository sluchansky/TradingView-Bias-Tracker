---
name: FVG result evidence identity
description: How FVG research variants enter the consolidated market-student evidence ledger.
---

Every FVG experiment variant is an independent research observation and must be resolved by its exact durable result identity. The coordinator's baseline opportunity is not a substitute for variant-level evidence.

**Why:** Strategy Lab completeness and restart-safe reconciliation require every terminal variant row, including pre-filtered no-entry rows, to have one unambiguous observation/outcome chain.

**How to apply:** Keep the adapter research-only and fail-open. Use exact result IDs for observation, reconciliation, and terminal outcome attachment; never correlate by time, price, or opportunity proximity.