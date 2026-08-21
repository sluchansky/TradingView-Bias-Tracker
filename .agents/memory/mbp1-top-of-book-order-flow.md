---
name: MBP-1 top-of-book Order Flow
description: Safe use of Databento MBP-1 best-bid/best-ask data in the advisory Order Flow layer.
---

MBP-1 is an additive subscription alongside the trade tape, not a replacement for existing price, CVD, VWAP, or bar ingestion. Only a valid, fresh per-instrument best bid/ask snapshot may produce book imbalance; missing, stale, malformed, or reconnect-era data is a no-op.

**Why:** Market-depth availability can vary by entitlement, feed health, and market activity. Reusing a disconnected session's displayed liquidity would make an advisory score misleading, and a book-stream failure must never interrupt the established trades feed.

**How to apply:** Keep the MBP subscription independently fail-open, clear snapshots at every connection start, keep freshness enforcement at the source boundary, and preserve the existing bounded directional Edge Score cap. Treat deeper MBP-10/MBO work as a separate enhancement rather than changing trade ingestion.