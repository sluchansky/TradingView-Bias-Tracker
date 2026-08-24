---
name: Market data display freshness
description: UI safety rule for presenting Databento and Visual Brain market data.
---

Treat a stale or unreachable market-data snapshot as unavailable in every operator-facing display. Databento needs a current connected event timestamp (with a bounded completed-bar fallback); Visual Brain needs a recent observation timestamp. A prior successful response must not remain visible as if it were current after polling fails.

**Why:** Old prices, VWAP, chart overlays, setup status, or Visual Brain bias can look actionable even though their source is no longer current. Display integrity must fail closed without changing the trading engine.

**How to apply:** On a failed request or stale freshness classification, clear chart series and derived overlays; suppress price, VWAP, setup, and active-trade presentation; label source, connection, and age explicitly. Keep this behavior display-only and never use it to alter qualification, risk, routing, execution, or coordinator behavior.