---
name: Databento soak evidence
description: How dispatcher pressure evidence must be captured for release readiness.
---

The market-data soak gate must sample queue and downstream peaks while records
are being admitted; a post-drain snapshot alone hides the pressure that the
bounded queues experienced. Supported-load assertions require zero drops and
freshness for every instrument, while overload assertions require explicit
unavailability rather than silent recovery.

**Why:** queue depth returns to zero as soon as a worker takes the final item,
even though the workload may have briefly approached its limit.

**How to apply:** keep soak workloads count-based and local-only, report
before/after telemetry plus sampled peaks and drain time, and test both the
supported budget and intentional fail-closed overload.