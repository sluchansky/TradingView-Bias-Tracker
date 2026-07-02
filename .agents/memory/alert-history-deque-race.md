---
name: ALERT_HISTORY deque race — readers MUST snapshot
description: Why every reader of the lock-free shared ALERT_HISTORY deque must iterate a list() snapshot, not the live deque, and why a lock is the wrong fix.
---

# ALERT_HISTORY is a lock-free shared deque — iterate a snapshot, never the live deque

`ALERT_HISTORY = deque(maxlen=1000)` has NO lock. The webhook worker thread appends
to it while `/status`, the heartbeat eval, and various display readers read it. Any
reader that does a **live Python `for` loop** over it (`for x in ALERT_HISTORY:` /
`reversed(ALERT_HISTORY)`) can crash with `RuntimeError: deque mutated during
iteration` → intermittent 500s on `/status` (dashboard "freezes") and
`Heartbeat evaluation failed for <inst>: deque mutated during iteration`.

**The rule:** every reader iterates a `list(ALERT_HISTORY)` snapshot. For callees that
receive the deque as a param (`evaluate_strict_setup`, `get_setup_stage`,
`_early_event_times` → `_early_latest_ts`), snapshot the param once at the top of the
function (`alert_history = list(alert_history)`) so all callers are covered.

**Why the snapshot is correct AND enough (not a band-aid):** `list(deque)` copies at
C level via `PySequence_List`, which runs no Python bytecode mid-copy, so under the
GIL no thread switch can happen during the copy → it is effectively atomic. A Python
`for` loop is the opposite: the interpreter releases the GIL between iterations (and on
slow calls like `datetime.fromisoformat`), letting a concurrent `.append()` bump the
deque's internal state, which the deque iterator's invariant check detects on the next
`__next__`. So the crash is a *live-loop* problem only; snapshotting eliminates it.
Snapshot content/order is identical → gate/scoring output byte-identical (goldens prove).

**Why NOT a lock:** adds lock-ordering risk in a codebase that already warns about lock
nesting, and serializes the hot readers (`/status`, heartbeat) against the webhook
ingest path, for zero correctness the GIL doesn't already give these ops. The
established idiom in this file is the `list()` snapshot (see the commented reader that
first documented it) — follow it.

**Caveat:** the atomicity argument is CPython-GIL-specific; it would NOT hold on a
free-threaded (no-GIL) build — there you WOULD need a lock.

**Known benign residual (do NOT "fix" with a lock):** the zone-broken prune does
`ALERT_HISTORY.clear()` then `.extend(kept)`. A reader that snapshots in that tiny
window sees a partial/empty history for ONE eval tick (wrong-for-one-tick, never a
crash). It runs in the webhook request path (single-writer in practice), so it is left
lock-free on purpose (comment is at the prune site).

**How to apply:** any NEW reader of ALERT_HISTORY (direct, or a function that receives
it) must iterate a `list()` snapshot. `len(ALERT_HISTORY)` is fine (atomic). Writers
(`append`/`clear`/`extend`) are left unlocked — the failure class is reader-side only.
