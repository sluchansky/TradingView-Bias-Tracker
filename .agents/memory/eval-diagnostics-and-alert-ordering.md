---
name: Eval diagnostics & alert-before-journal ordering
description: The webhook decision worker must send the trade alert before any journal Discord post; the journal-channel embed is deferred to a slow-task worker. Plus how per-evaluation timing metrics are collected without affecting read-only callers.
---

# Alert-before-journal ordering (perf contract)

On the serialized webhook decision worker, the live trade card (`send_live_ready_card`)
must be sent **before** any Discord journal post, and the slow journal-channel embed
(`send_journal_discord_embed`) is offloaded to a single-FIFO background "slow-task"
worker (`_enqueue_slow`). The in-memory journal store (`create_journal_entry`) still
runs synchronously on the decision worker — only its Discord embed is deferred via the
`post_discord=False` param.

**Why:** Discord posts + screenshot/AI enrichment are slow (hundreds of ms to seconds).
If they run inline before/around the alert, the trade alert and the *next* alert's
decision get queued behind them, delaying live signals. Keeping the in-memory store on
the decision worker preserves the existing no-race guarantee (JOURNAL/JOURNAL_KEYS dedup
stays serialized); only the network embed is safe to defer.

**How to apply:** Any future change in the webhook tail must keep this order: build+store
journal (in-memory) → send live card → `_enqueue_slow(journal embed)`. Wrap the live-card
send in its **own** try/except so a card failure can't suppress the deferred journal embed,
and isolate `create_journal_entry` failures so they can't crash the worker or block the alert.

# Per-evaluation timing metrics

Phase timings are gathered with a thread-local `_timed(key)` contextmanager whose
accumulator is armed by `_eval_timing_begin()` **only** on the webhook decision worker.
For read-only callers (dashboard `/status`, `/why`, periodic loops) there is no
thread-local accumulator, so every `_timed()` block is a **no-op** — they are never slowed
or polluted by diagnostics. `_record_eval_metrics` appends one record per scored alert to
`EVAL_METRICS` (deque maxlen 100); a phase that didn't run for that alert (e.g. no journal
write on a WAIT) is left `null`.

**Why:** want low-overhead per-phase timing without branching every call site on "are we
diagnosing", and without read-only dashboard polls inflating the numbers.

**How to apply:** append/snapshot of `EVAL_METRICS` is guarded by `EVAL_METRICS_LOCK`
(`list(deque)` can raise "deque mutated during iteration" vs a concurrent append). Metrics
are recorded on both webhook exit paths (zone-mitigated early return AND normal end) but not
on a `full_analysis` exception — acceptable (the alert itself failed), add a failure metric
only if observability needs it. `/eval-metrics` (JSON) + `/diagnostics-live` (HTML, 1s poll)
are owner-only: whitelisted in the Express flask-proxy but deliberately NOT in `OPEN_PATHS`.
