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