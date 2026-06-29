---
name: Opposite-side reversal buffer
description: TradersPost-only per-instrument buy<->sell send spacing in the broker sink; why it reserves under lock and never buffers exits.
---

# Opposite-side reversal buffer

`BROKER_OPPOSITE_SIDE_BUFFER_SEC` (env, default 0 = OFF) enforces a minimum gap
between an opposing buy<->sell LIVE broker send and the previous one for the SAME
instrument, so TradersPost accepts the reversal instead of rejecting the too-soon
opposing order. Lives in the single shared sink `_send_broker_order` (so BOTH the
single-order gateway and the LIVE 2-contract runner legs are covered), placed AFTER
payload validation (a blocked/invalid payload never sleeps) and BEFORE the POST.

**Rule — reserve, don't read-sleep-record.** Under `_BROKER_SIDE_LOCK` compute an
absolute `send_at` (= `max(now, prev_send_at + buf)` on an actual side flip, else
`max(now, prev_send_at)`), STORE it in `_BROKER_LAST_SIDE[instrument]` BEFORE
releasing the lock, then sleep until `send_at` outside the lock.

**Why:** a plain read-sleep-record (read last side, release lock, sleep, then record)
lets a second same-instrument request slip through while the first is sleeping and
POST an opposite side closer than `buf` — exactly the spacing the feature exists to
prevent. Reserving the future send time under the lock makes each opposite flip chain
off the previous reservation, so concurrent buy/sell/buy are scheduled `buf` apart.

**How to apply / invariants:**
- TradersPost-only (`mode != "traderspost"` is inert) — the rejection is a TradersPost
  behaviour; never alter PickMyTrade timing.
- A flatten/exit (`_broker_payload_side` -> None) is NEVER delayed and never touches
  the tracker; only directional buy/sell entries are spaced. A protective close must
  always go instantly.
- Same-direction repeats are never delayed (gap 0).
- Buffer is fail-OPEN (any error skips it); the broker SEND stays fail-CLOSED. A buffer
  hiccup must never block or delay a live order beyond the intended wait.
- Default 0 = OFF = byte-identical to pre-feature (no sleep, no tracking, no logging).
- The sleep runs in the webhook worker thread; acceptable because it only triggers on
  a same-instrument opposite flip within the window (rare).
- Deterministic test trick: freeze `time.time()` to simulate concurrent arrivals and
  assert the reserved waits chain (e.g. buy/sell/buy -> slept [buf, 2*buf]).
