---
name: Live alert trade-card
description: The clean trade-card is the single alert format for both journal and main Discord channels; how/when it fires and the throttle invariant.
---

# Live alert trade-card

The clean "AI Trading Partner · {Strong|Possible} Trade Detected" card is the
single alert format for BOTH the journal channel and the main/live channel. There
is one shared embed builder and one shared entry builder; the journal card and the
live card must stay visually identical apart from the footer.

## When the live (main-channel) card fires
- **Instant**: once per NEW qualifying setup, fired from the /webhook path only
  when the verdict is `LONG READY` / `SHORT READY` AND no trade is active. Dedup is
  inherited from the journal entry creation, so a repeated webhook for the same
  setup does NOT re-alert.
- **Recurring**: a background loop re-posts the card every `TRADE_READY_INTERVAL`
  (default 300s, env-overridable) while the setup stays READY — this is the user's
  "update every 5 mins" requirement.

## Invariants any change here must keep
- The main channel must NEVER post the old verbose per-webhook embed again (it was
  intentionally replaced; the verbose sender is left defined only for rollback).
- Live cards fire ONLY on READY verdicts and ONLY when no trade is active.
- **Throttle**: a per-instrument last-send timestamp suppresses the periodic loop
  from posting within `TRADE_READY_INTERVAL` of any prior card (instant or
  periodic). Without it, a setup that turns READY just before a timer tick would
  produce an instant card + a periodic card seconds apart.
- Routing is per-instrument (MNQ → MNQ channel, else default); never log the
  webhook URLs (they are secrets).

**Why:** the user wants the main channel to be a clean, low-noise signal feed
(one card per real setup, refreshed on a fixed cadence), not a verbose firehose.
