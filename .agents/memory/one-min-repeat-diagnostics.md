---
name: 1-min repeat alert diagnostics layer
description: How the bot copes with TradingView alerts that fire AND repeat every minute — dedup, setup-state, latency are diagnostic-only and must never touch the trade decision.
---

TradingView now fires alerts every 1 min AND re-fires every minute while the
condition still holds. The handling layer for this (signal dedup, setup state
machine, latency logging, last-hour stats, rejection counters) is **purely
additive / observability**. It must never change the gate, scoring, READY verdict,
Edge Score, EARLY intrabar feature, or SWING parity, and `full_analysis()` stays
side-effect-free.

## Inbound dedup is COUNTING-ONLY, never outbound suppression
A repeated webhook within `SIGNAL_DEDUP_COOLDOWN_SEC` (default 240s) is flagged
`is_duplicate` and bumps `duplicates_ignored` + `wait_reasons_breakdown["cooldown_duplicate"]`.
It is keyed on `(instrument, normalized alert_type)` — the alert_type already
encodes direction/setup (e.g. CHOCH DEMAND vs SUPPLY), so a direction flip is a
different key and is never a duplicate. The job is **always enqueued** and
`full_analysis()` **always runs** (repeats must still refresh price/VWAP/state).

**Why no outbound dispatch suppression:** the existing zone-aware
`create_journal_entry` dedup + the READY re-post throttle (re-post interval > the
240s cooldown) already prevent duplicate Discord posts while still allowing a
legit *new-zone* READY through. A coarse `(instrument, alert_type)` suppression
guard would risk swallowing a real new-zone READY, and any change near dispatch
risks the EARLY byte-for-byte invariant. So dedup feeds counters + the state
machine only.
**How to apply:** if asked to "stop duplicate alerts", do NOT add a suppression
gate here — first prove the downstream journal/READY/EARLY throttles are
empirically failing. The dedup counter is for *visibility*, not control.

## Setup state machine is display-only, derived POST-analysis
`_update_setup_state()` runs AFTER `full_analysis()` from the worker + heartbeat
consumers, never inside `full_analysis()`. Live states (FORMING/READY/ACTIVE) are
**sticky**: a heartbeat or duplicate re-eval never downgrades them (no
READY<->FORMING flap on 1-min repeats). READY->ACTIVE requires a live card
actually dispatched on that eval, not just a READY verdict. A state leaves live
only via upgrade, INVALIDATED (zone broken/mitigated or opposite-direction READY),
or EXPIRED (held > `SETUP_STATE_TTL_SEC`, default 1800s).
**Known display caveat (intentional, not a bug):** INVALIDATED/EXPIRED are NOT
permanently sticky — once the invalidating condition clears (e.g. zone_broken
expires) and a fresh candidate direction exists, a heartbeat can move them back to
FORMING. This is correct for a continuously-evaluating live bot (a genuinely new
setup is forming); don't "fix" it into permanent stickiness without a reason.

## Lock ordering (if ever nested)
DEDUP_LOCK -> STATE_LOCK -> EVAL_METRICS_LOCK -> COUNTERS_LOCK (COUNTERS last;
never acquire COUNTERS_LOCK inside EVAL_METRICS_LOCK). In practice each critical
section is tiny and released before taking the next, so no nesting today.

## Last-hour stats
`alertsReceivedLastHour` / `duplicatesIgnoredLastHour` come from two small deques
(`_WEBHOOK_TS`, `_DUP_TS`) trimmed to a 1-hour window under COUNTERS_LOCK.
`signals_passed_filters` / `signals_rejected` are counted ONLY for real webhooks
(trigger=="webhook") and ONLY when not a duplicate — heartbeats and duplicates are
excluded so the funnel reflects distinct TradingView signals.
