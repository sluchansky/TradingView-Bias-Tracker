---
name: Databento bounded backpressure
description: Fresh-only market data must be source-time based and reconnects must never overlap record consumers.
---

Databento intake uses a bounded, ordered dispatcher. Queue overflow or a disconnect makes the affected instrument unavailable to fresh-only consumers; it must never be treated as merely newly processed market data.

**Why:** Local processing timestamps can make delayed trade, bar, CVD/RVOL, VWAP, MBP-1, and structure data appear current after a backlog. Reconnect overlap can also reorder or mix state across sessions.

**How to apply:** Preserve one state-mutating record consumer per live session. Disconnect before draining, fence the old session, and wait for its in-flight handler before starting a replacement. Carry source event time into every freshness calculation, including shadow/canonical displays.