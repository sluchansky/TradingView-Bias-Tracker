---
name: Authoritative verdict history
description: Durable constraints for recording final strategy verdicts without changing the trading path
---

The final SCALP and INTRADAY_TREND verdict can be recorded as an observer-only,
append-only history stream after all vetoes and market-closed overrides resolve.
The observer must snapshot through a bounded non-blocking queue, use deterministic
chained identity so repeated snapshots deduplicate while a return to an earlier
state appends, and restore the latest chain state after restart.  Database-bound
scalar columns must normalize live result objects (for example, a structure cycle
status object) before insertion; preserve richer context only in JSONB fields.

**Why:** Live operator responses contain structured status objects in fields that
look scalar, and relying on psycopg adaptation can silently drop every observer
write while the trading service itself continues to operate normally.

**How to apply:** Keep this module outside gates, scoring, risk, execution,
coordinator, ghost authority, and SWING evaluation.  Apply its schema externally
in development and through the Publish schema diff for production; app startup
may probe/read but must not create or mutate schema.

Read-only chain audits must use immutable append order, not wall-clock timestamps.
A bounded latest window is PARTIAL only when one extra fetched row proves the
first retained row's predecessor; an absent or mismatched predecessor is BROKEN.

**Why:** Concurrent observers can assign recording timestamps in the opposite
order from the lock-protected predecessor chain, and blindly treating any
non-null first predecessor as truncated can hide real chain corruption.

**How to apply:** Order operator/audit windows by durable event identity, fetch
one row beyond the displayed limit, and validate that extra row against the
first displayed previous key. Keep legacy reconstruction ordering unchanged
unless separately audited.