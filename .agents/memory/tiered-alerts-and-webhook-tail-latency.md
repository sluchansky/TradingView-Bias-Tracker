---
name: Tiered WATCH/ARMED alerts & webhook-tail latency
description: The SCALP early-alert ladder is display-only, and any Discord POST added to the /webhook tail must be offloaded to the slow-task worker, never run inline.
---

# Tiered alerts & the webhook-tail latency rule

## alert_level is display-only
`full_analysis` sets `result["alert_level"]` (READY / ARMED / WATCH / None) AFTER the
verdict dict is built. It is additive and read only by the tiered dispatcher, the
diagnostics metrics record, and the diagnostics HTML — never by any decision path.
**Why:** SWING mode must stay byte-for-byte identical in DECISION (verdict / direction /
score / ready). A diagnostic or alert field that leaks into the decision breaks that.
**How to apply:** keep alert_level (and any future "stage"/"tier" field) out of
`decision_engine`, candidate selection, and scoring. Add such fields as additive keys only.

## Any Discord POST in the webhook tail MUST be offloaded
The `/webhook` heavy tail (`_process_webhook_alert`) runs on a single FIFO worker that
processes alerts serially. A synchronous `requests.post(..., timeout=10)` there blocks
the next evaluation and can delay the journal enqueue.
**Why:** an inline tiered-alert POST was caught (architect review) delaying the journal
enqueue and the next webhook by up to the 10s timeout. The READY live card is the ONLY
alert allowed to send inline (latency-critical, fired first); everything else — journal
embed and tiered WATCH/ARMED — goes through `_enqueue_slow`.
**How to apply:** compute throttle state + build the embed synchronously, then
`_enqueue_slow(lambda: _post(...))`. Record throttle timestamps BEFORE enqueue so a post
failure can't cause a per-webhook retry storm. `.local/test_gate.py` has a regression
test (monkeypatch `requests.post` to hang) that locks this non-blocking behavior.

## Tiered throttle contract
Per-instrument `LAST_TIER_LEVEL` / `LAST_TIER_AT`: a WATCH/ARMED level fires on a level
transition OR after `WATCH_ARMED_COOLDOWN_SEC` while it persists; READY is recorded (so a
later retreat to ARMED/WATCH counts as a fresh transition) but NOT posted (the live READY
card owns it); a None level clears the state. Routing via env `TIERED_ALERT_CHANNEL` =
journal (default) | main | none, so the main signal channel stays READY-only.
