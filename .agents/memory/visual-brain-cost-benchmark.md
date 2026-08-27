---
name: Visual Brain cost benchmark
description: Durable isolation and accounting rules for Visual Brain trigger-policy and candidate-cost experiments.
---

The Visual Brain cost benchmark must remain default-off, in-memory, observation-only telemetry. It may measure baseline retries/tokens/cost, image and completed-bar fingerprints, deterministic event-trigger projections, heartbeat use, and optional paired candidates, but it must never suppress or alter the canonical GPT call.

**Why:** Cost-policy evidence must be collected without changing Visual Brain cadence, model behavior, observations, persistence, Market Student, coordinator state, alerts, READY decisions, execution, risk, or broker routing.

**How to apply:** Build candidate inputs from immutable copies of the same screenshot/context/history used by baseline. Run candidates only after the canonical observation is persisted and cached, on bounded asynchronous workers with fail-open busy skips. Keep result stores bounded and account explicitly for late results whose cycle record was evicted. Candidate output is report-only and must never be written back to any canonical store.