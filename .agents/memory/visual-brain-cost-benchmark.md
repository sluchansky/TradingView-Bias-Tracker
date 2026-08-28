---
name: Visual Brain cost benchmark
description: Durable isolation and accounting rules for Visual Brain trigger-policy and candidate-cost experiments.
---

The Visual Brain cost benchmark must remain default-off, in-memory, observation-only telemetry. It may measure baseline retries/tokens/cost, image and completed-bar fingerprints, deterministic event-trigger projections, heartbeat use, and optional paired candidates, but it must never suppress or alter the canonical GPT call.

**Why:** Cost-policy evidence must be collected without changing Visual Brain cadence, model behavior, observations, persistence, Market Student, coordinator state, alerts, READY decisions, execution, risk, or broker routing.

**How to apply:** Build candidate inputs from immutable copies of the same screenshot/context/history used by baseline. Run candidates only after the canonical observation is persisted and cached, on bounded asynchronous workers with fail-open busy skips. Keep result stores bounded and account explicitly for late results whose cycle record was evicted. Candidate output is report-only and must never be written back to any canonical store.

Advisory comparisons must use only exact paired baseline/candidate cycles. A candidate sample is representative only when the paired subset—not the larger baseline window—covers the required instruments and labeled market sessions; schema validity is compared per completed run while retries remain a separate metric.

**Why:** Mixing unpaired baseline cycles into candidate cost or quality denominators can make a sparse candidate look cheaper or more reliable than it is.

**How to apply:** Expose unpaired coverage separately, gate confidence on paired coverage, and keep every recommendation advisory with no automatic rollout.

Candidate execution must remain disabled whenever the Visual Brain 2.0 event gate is enabled, even if an old benchmark candidate flag is present.

**Why:** Visual Brain 2.0 explicitly preserves GPT-5.4 as the sole paid observer and excludes candidate/local-model rollout; telemetry must never imply a candidate is off while still executing it.

**How to apply:** Candidate benchmark tests may opt into the legacy benchmark-only mode explicitly, but the event-driven runtime must report and enforce candidate-off behavior.