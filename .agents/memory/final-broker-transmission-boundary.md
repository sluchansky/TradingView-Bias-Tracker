---
name: Final broker transmission boundary
description: Required last-mile broker safety contract for all entry and exit transmissions.
---

Every broker-capable path must reach one shared final boundary immediately before
the broker HTTP request. Entry contexts must prove server-built provenance,
canonical instrument/symbol, explicit open session, current server freshness,
exact direction/quantity/bracket, idempotency reservation, and the applicable
operator or autonomous arm policy. Exit contexts must be non-reversing and bind
to an exact tracked position or runner identity.

**Why:** Gateway-level validation alone is vulnerable to future direct callers,
state races, stale data, and payload adaptation errors. A last-mile check keeps a
single broker HTTP sink from becoming a bypass.

**How to apply:** Route new broker sends through the shared sink with a typed
context; do not add direct POSTs. Preserve provider payload shape and do not
mutate a valid payload. If reversal spacing reserves state before the final
decision, keep it provisional and only retain it for sends that might have
reached the provider; roll it back after locally proven no-send outcomes.