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

At boot, a table-readiness probe alone is insufficient evidence of durable
continuity. If the historical evidence restore fails, disable the evidence
persistence writer and expose the unavailable state rather than accepting new
in-memory snapshots as durable.

**Why:** A process can otherwise report healthy persistence after a restart
while it has lost the exact chain needed to link new observations and outcomes
to its prior evidence.

**How to apply:** Restore matched records before unmatched records, and only
keep the writer attached after that read succeeds. The legacy lifecycle remains
unchanged; this protection affects the shadow projection only.

For cross-table unmatched-to-matched links, persist the matched snapshot first
and create the link only after that write succeeds. A database no-op is a safe
replay only when the stored provenance fingerprint matches; a conflicting
fingerprint must stay pending and fail closed. On restart, validate even a
populated link against the restored exact lane/source/result identity and expose
invalid links as unresolved rather than trusting the stored ID.

**Why:** Independent writes can crash between the parent and link, and a
conditional upsert can otherwise hide a provenance conflict or leave a phantom
target that looks resolved in health reporting.

**How to apply:** Keep the deferred additive foreign key for future link writes,
but retain runtime restore validation because historical rows may predate its
enforcement. Never replace a distinct matched evidence ID for the same
unmatched identity.

Strict-link health verification must use a fresh local authority with an
in-memory append callback. Its link predicate is the exact, nonempty
instrument plus canonical mode, coordinator opportunity, and declared generic
source identity; absent instrument data is rejected rather than compared as
equal.

**Why:** A different coordinator ID only proves opportunity isolation, not
instrument isolation. A runtime health endpoint must prove the real matcher is
safe without touching durable evidence or trading state.

**How to apply:** Exercise a same-opportunity cross-instrument collision,
cross-mode and malformed references, blank identities, reference-before-
authority relinking, and restore-then-replay. Treat the verifier as healthy
only when restore writes nothing on replay and its local callback receives no
post-restart duplicate events.