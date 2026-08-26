---
name: Durable paper-simulation history
description: Safety rules for resolving paper outcomes from retained completed bars across restarts.
---

Paper outcomes may use durable history only when every completed Databento bar from the entry boundary through the terminal bar belongs to one live capture session with a contiguous per-instrument sequence. A retained pre-entry bar must prove the first post-entry sequence; sequence 1 is valid without a predecessor only when that capture session began before entry.

**Why:** Timestamp gaps are ambiguous because a quiet market may legitimately produce no one-minute bar, while persistence failure, downstream queue loss, reconnects, and restarts can also create gaps. Treating a later stop/target hit as definitive after an unobserved interval can invert the real stop-first result.

Historical startup replay is never paper-resolution evidence, and durable pre-restart bars are never used as the latest bar for managed/live positions. A discontinuity may still preserve a terminal outcome proven before the gap; otherwise the row stays open until max hold and then becomes explicitly unresolved.

The only exception is explicit operator repair of a terminal unresolved paper row. That path may request a bounded Databento Historical backfill, but client-supplied OHLC is never evidence: the server must fetch or reload persisted verified records, keep the original unresolved audit, and write only the originating paper ledger.

**Why:** A client-controlled “verified” label can turn fabricated prices into research outcomes, while the normal paper close helpers also publish learning evidence. Server-owned provenance and a direct isolated update prevent both failures.

**How to apply:** Restart recovery must stay live-capture-only. Operator repair must fail closed when the verified store or Databento history is unavailable, use exact server-held continuity metadata, fingerprint authoritative bars for idempotency, and never call live execution or learning-ledger writers.