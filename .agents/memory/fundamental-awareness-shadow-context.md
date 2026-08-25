---
name: Fundamental awareness shadow context
description: Rollout contract for scheduled-event context beside the technical verdict.
---

Phase 1 fundamental awareness is a display/logging-only normalized context for
scheduled US high-impact CPI, PPI, Employment/NFP, PCE, GDP, FOMC, FOMC press
conference, and Fed Chair speech events. It must use the existing asynchronous
calendar cache rather than perform a provider call during an evaluation.

**Why:** The operator needs event-risk visibility without introducing a second
evaluator or allowing stale/malformed provider data to influence a live
decision.

**How to apply:** Keep both the master feature flag and explicit shadow flag
off by default; require both before exposing the context. Preserve fail-open
UNKNOWN/NEUTRAL output for unavailable, stale, malformed, or no-event data.
Do not feed this context into verdicts, scores, qualification, risk, sizing,
execution, alerts, schedulers, coordinators, persistence, or schema changes
unless a separately approved phase changes that contract.