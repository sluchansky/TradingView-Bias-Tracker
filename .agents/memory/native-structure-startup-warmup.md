---
name: Native structure startup warm-up
description: Rules for reconstructing native detector state from Databento history without turning past bars into live trade evidence.
---

Warm native structure state only from real, closed, strictly ordered OHLCV history. Validate every bar and make empty, malformed, insufficient, future-dated, or excessively stale history explicitly unavailable per instrument. Preserve the source response order—never sort a malformed feed response into a valid replay sequence.

**Why:** Replaying old bars through the live detector can otherwise emit BOS/CHOCH/confirmation alerts and callback fan-out as if they occurred after boot, creating false current evidence. Starting the live stream while replay runs can also make state ordering non-deterministic or fill the bounded intake queue.

**How to apply:** Complete bounded history replay before the live subscription. Reuse the detector's native bar-close path to rebuild its private state, but suppress alert-history writes and callbacks during replay. Per instrument, surface `WARMING_UP` / `READY` / `UNAVAILABLE`, actual seeded bars (not merely received bars), newest source timestamp, completion/failure reason, and monotonic duration as operator health metadata only; never alter the resolver's actual state, scoring, gates, risk, or execution.