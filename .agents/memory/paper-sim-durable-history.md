---
name: Durable paper-simulation history
description: Safety rules for resolving paper outcomes from retained completed bars across restarts.
---

Paper outcomes may use durable history only when every completed Databento bar from the entry boundary through the terminal bar belongs to one live capture session with a contiguous per-instrument sequence. A retained pre-entry bar must prove the first post-entry sequence; sequence 1 is valid without a predecessor only when that capture session began before entry.

**Why:** Timestamp gaps are ambiguous because a quiet market may legitimately produce no one-minute bar, while persistence failure, downstream queue loss, reconnects, and restarts can also create gaps. Treating a later stop/target hit as definitive after an unobserved interval can invert the real stop-first result.

Historical startup replay is never paper-resolution evidence, and durable pre-restart bars are never used as the latest bar for managed/live positions. A discontinuity may still preserve a terminal outcome proven before the gap; otherwise the row stays open until max hold and then becomes explicitly unresolved.

**How to apply:** Any paper ledger that reads retained bars must carry capture-session identity and monotonic sequence metadata, prove the entry boundary, stop evaluation at the first discontinuity, and expose missing coverage as health—not fabricate an expiry or fetch a second source.