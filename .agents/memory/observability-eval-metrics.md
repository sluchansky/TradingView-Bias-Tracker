---
name: Observability / eval-metrics heartbeat
description: Why the diagnostics eval count looked low, and the invariants the timer-driven re-eval + counters must keep.
---

# Eval-metrics observability (Diagnostics page)

The Diagnostics page reads EVAL_METRICS. Historically that buffer only got a row
on **webhook-triggered** scoring, so a quiet market showed almost no evals ("6 in
25m") even though `/status` re-runs `full_analysis` every ~3s for display. `/status`
does NOT record — display and metrics are deliberately separate.

A **heartbeat eval loop** (distinct from the Discord heartbeat check-in) now calls
`full_analysis` on a timer for each instrument and records the result, so the page
reflects continuous evaluation even with zero webhooks.

## Invariants any future change here must keep

- **Heartbeat path is diagnostic-only.** It may call ONLY `full_analysis(ticker_override)`
  + `_record_eval_metrics(..., trigger="heartbeat")`. It must NEVER enter the
  Discord / journal / EARLY / tiered / live-card paths, or it double-posts. READY
  re-posts are already owned by the trade-ready loop.
  **Why:** timer-driven re-eval is safe *only because* `full_analysis` is
  side-effect-free (writes no globals). If you ever make `full_analysis` mutate
  state, the heartbeat becomes a silent state-corruption source.
- **Lock ordering:** never acquire COUNTERS_LOCK while holding EVAL_METRICS_LOCK.
  Update counters after the metrics append, in a separate critical section.
  **Why:** nesting the two invites deadlock under webhook+heartbeat concurrency.
- **ready_setups_detected** counts only non-READY→READY *transitions* per
  instrument (shared `_READY_STATE_BY_INST`), so webhook + heartbeat don't both
  tally a persistent READY.
- **waitReason source:** the human WAIT reason is at `full_analysis` top level under
  **`strict_reason`**, NOT `reason`. A plain `"reason"` key only exists nested inside
  `trade_plan`. `a.get("reason")` at the top level is always None.

**How to apply:** when adding any new diagnostic that re-runs scoring on a timer or
records to EVAL_METRICS, route it through `_record_eval_metrics` only, keep counter
mutation outside EVAL_METRICS_LOCK, and pull the WAIT reason from `strict_reason`.
