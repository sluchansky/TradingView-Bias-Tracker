---
name: Per-instrument MUTE ALERTS (MGC/MNQ)
description: How the per-instrument alert mute works — what it suppresses, what it must NEVER touch, and the instrument-resolution rule any new send site must follow.
---

# Per-instrument MUTE ALERTS

A server-side, in-memory per-instrument flag (`ALERTS_MUTED`/`ALERTS_MUTED_LOCK`,
read via `_alerts_muted(...)`) that suppresses ONLY the Discord NEW-SETUP
notifications for the muted instrument. Driven from the dashboard "Focus" row
(unchecking an instrument hides its tab AND mutes it) and the owner-only
`/alerts/mute` route (GET state / POST `{instrument, muted}`). `/alerts/mute` is
in the Express proxy whitelist (exact-path match — `/alerts` does NOT cover it)
but deliberately NOT in OPEN_PATHS.

**The rule:** muting must change ONLY whether NEW-SETUP Discord sends go out.
- Muted senders (NEW-SETUP only): live READY card (incl. @everyone + A+ mirror),
  EARLY teaser, tiered WATCH/ARMED, new-entry journal embed, zone-mitigated notice.
- NEVER muted: anything for an ALREADY-ACTIVE position — `send_trade_event_message`,
  `_send_management_update`, `_send_outcome_update`, `_update_journal_outcome`, and
  the `/enter` `/close` `/breakeven` execution paths. A live position's stop/target
  must always alert.
- NEVER touched by mute: gate/scoring/sizing/dedupe/broker, `full_analysis`, the
  journal RECORD (created with `post_discord=False` separately from the embed),
  `_register_managed_trade`, the LAST_READY snapshot, eval-metrics/diagnostics. A
  muted instrument is evaluated and tracked exactly like an un-muted one, silently.

**Why:** the user wants to silence one market's noise without creating a data gap
or weakening any safety/accounting — research/tracking must stay complete.

**How to apply (any future NEW-SETUP send site):**
- Gate it on `_alerts_muted(...)` and resolve the instrument from
  `ticker or instrument or alert_type` — NOT `ticker` alone. Webhook-built records
  carry `ticker=None` for title-resolved alerts while `instrument` (resolved_inst)
  and a prefixed `alert_type` still name MNQ/MGC; checking ticker alone leaks a
  muted notice. (This exact bug bit `send_zone_mitigated_message`.)
- `_alerts_muted` resolves via `_instrument_from_text` (returns MGC/MNQ only when
  UNAMBIGUOUS, else None → treated as NOT muted). Fail-safe is always toward
  ALERTING — never silence by accident.
- If the send drives accounting (alert_sent_at / alerts_sent / READY→ACTIVE), have
  the sender RETURN whether it actually dispatched and gate the accounting on that
  (see `send_live_ready_card` → `dispatched` bool), so a muted instrument doesn't
  inflate counters/state.
- State is in-memory and resets to unmuted on restart/republish — intentional
  (a stale silent mute dropping real alerts forever is worse than re-enabling).
