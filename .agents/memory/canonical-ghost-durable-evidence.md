---
name: Canonical Ghost durable evidence
description: Rules for the one-record, restart-safe Canonical Ghost evidence projection.
---

Canonical Ghost evidence is a separate shadow projection with exactly one
durable record for each eligible `generic_ghost` result in the `SCALP` or
`INTRADAY_TREND` lane. Its durable identity must retain the exact source result,
coordinator observation/opportunity, canonical observation/opportunity, and
strategy version. Never admit a lane based on a strategy name or fuzzy
time/price correlation.

**Why:** The existing generic ghost lifecycle remains the only observation and
outcome authority. A one-row evidence boundary makes the whole copied chain
restart-safe without redirecting legacy writers, outcome resolution, learning,
promotion, gates, routing, broker calls, or execution.

**How to apply:** Keep append-only reconciliation events as history and the
evidence row as the latest copied snapshot. Terminal updates must use a
deterministic outcome fingerprint plus an explicit ordering key: replayed or
older copies collapse, while a later copied correction replaces only the
snapshot. Persist and deduplicate on the exact natural source identity, restore
all records before recovery, and make any database failure visible/retryable
without changing the legacy lifecycle.