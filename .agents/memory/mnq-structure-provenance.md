---
name: MNQ structure provenance tracing
description: Rules for the shadow-only Databento pivot/BOS/CHOCH diagnostic audit trail.
---

Structure provenance is an observational, bounded, in-memory trace only. It records detector inputs and decisions, then copies the already-authoritative analysis cycle and structure gate result for auditability; it never becomes a trading input.

**Why:** Bar-close analysis runs asynchronously. A newer bar can record its own trace before an earlier analysis finishes, so annotating the newest trace would silently pair a decision with the wrong market event. Bar timestamps can also repeat during replay or malformed input.

**How to apply:** Generate an opaque unique trace ID at detector-record creation, capture it synchronously before launching asynchronous work, and perform annotation by that exact ID while holding the provenance lock. Represent missing bar history as explicitly unavailable rather than as rejected or negative structure evidence.