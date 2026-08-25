---
name: Structure confirmation lag diagnostic
description: Research-only reconstruction of high-score candidates waiting on structure confirmation.
---

The confirmation-lag report must be reconstructed from immutable final verdict
snapshots and source timestamps. It classifies a blocked high-score candidate as
confirmed continuation, expiry, source-data delay, detector no-update, or an
active wait, while preserving the exact outstanding BOS/CHOCH event.

**Why:** Operators need to distinguish deliberate selectivity from a stale
detector before changing structure gates; using live mutable state would make
historical elapsed-time evidence non-reproducible.

**How to apply:** Keep the reducer and GET surface outside scoring, gate,
execution, and research authority. Treat source timestamps as evidence time and
recorded timestamps only as the latency comparison.