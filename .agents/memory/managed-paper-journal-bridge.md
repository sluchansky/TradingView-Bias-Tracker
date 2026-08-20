---
name: Managed paper journal bridge
description: How display-managed trades become paper rows without duplicating actual gateway paper executions.
---

# Managed paper journal bridge

Managed display/research trades for MGC, MNQ, MES, and MYM are journaled as explicit
`PAPER` native-journal records with a stable UUID derived from the managed trade key.
They are not broker orders, and their native-journal creation does not link to the
edge ledger.

**Why:** The managed watcher is what produces the visible strategy performance, but
those outcomes historically appeared only in the research table. A broad
instrument/direction close can select the wrong same-side trade, and a paper gateway
snapshot plus a managed display mirror would otherwise create duplicate records.

**How to apply:** Attach a gateway snapshot's native-journal ID to the matching
managed trade before the watcher can create its display-paper row. For paper-managed
closes, write the outcome by that exact ID and skip the broad legacy close fallback.
Keep execution source/initiator distinct from execution mode: `mode=paper` must
always render as the `PAPER` source label even when the initiator is `auto` or
`manual`.

## Restart safety

The recovery snapshot must be durable before a managed PAPER trade can advance, and it
must include every field that can change a future lifecycle decision — including
trailing high/low-water marks. Failed durability writes must remain retryable rather
than being acknowledged in memory.

**Why:** A restart can otherwise alter an open trade's management outcome (or make it
impossible to restore), despite no market or operator action occurring.

**How to apply:** When changing managed-trade lifecycle logic, audit every mutable
input it reads, carry it through the recovery snapshot, and add a before/after restart
decision-continuity test. Preserve valid stacked setups rather than collapsing them by
instrument during recovery.

An explicit operator stop is terminal local state: it must remain stopped after a
restart without creating a broker order or synthetic performance result. When one
managed trade can be restored from more than one durable source, reconcile the records
so the exact journal identity used for finalization is never lost.

**Why:** Treating an operator stop as in-memory-only resurrects a trade after reboot;
discarding identity during multi-store recovery risks closing a different stacked paper
row.

**How to apply:** A stop needs an independent durable intent fence as well as its
native-journal terminal write: if native persistence is transiently unavailable, boot
must honor the fence and retry cancellation instead of restoring the old open snapshot.
Test stopped-trade recovery and the real loader ordering whenever a new persistent
source is introduced.

Terminal outcomes need the same protection: persist a terminal intent fence before the
exact-row outcome write, keep the already-closed trade inert while retrying a failed
write, and never rehydrate a fenced row as open. Gateway PAPER linkage is valid only
after its native-journal creation confirms success.

**Why:** A fail-open terminal write otherwise turns a completed paper trade back into
an open one after restart; attaching an ID before its insert succeeds leaves a trade
pointing at a row that never existed.

**How to apply:** The terminal fence needs its own immutable outcome payload (result,
exit, P&L, R, and close metadata), not just open-state recovery fields. Test
close-write outages across both a live-process retry and a restart, and fault-inject
gateway creation before accepting any linkage behavior. Cache-fenced stops likewise
stay inert and retry native cancellation until it confirms terminal state.

PAPER management must not advance while its recoverable journal state is unconfirmed:
creation/linkage plus state persistence are prerequisites to watcher evaluation, and a
failed non-terminal post-evaluation state write must restore the prior managed state.
Notifications and dependent tracking changes must wait until that state is durable.

**Why:** Retrying a write alone is not lossless if the runner/partial lifecycle is
allowed to move ahead in memory before the write succeeds; a restart would recover an
older state or no trade at all.

**How to apply:** Any new managed PAPER transition must be tested with failed initial
insert, failed linked-row state write, and failed post-transition update. Pause or
rollback the transition; never let a fail-open persistence helper authorize progress
or publish a lifecycle event. Fenced terminal and stopped trades stay inert pending
native confirmation; unfenced terminal results roll back before outcome effects.