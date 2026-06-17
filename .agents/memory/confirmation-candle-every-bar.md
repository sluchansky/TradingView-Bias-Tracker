---
name: Confirmation Candle alerts are every-bar noise
description: Why "{INST} BULLISH/BEARISH CONFIRMATION" webhooks must be timestamp-anchored after structure, not treated as presence.
---

The TradingView "Confirmation Candle -> Webhook" indicator marks a shape on EVERY
5-min bar (bullish on green candles, bearish on red), so it fires a
`{INST} BULLISH CONFIRMATION` / `{INST} BEARISH CONFIRMATION` webhook on essentially
every bar. It CANNOT be made sparse on the TradingView side — its only Input is
"Instrument" (no strength/sensitivity filter), and its alert can only trigger on
"Shapes". This is a confirmed dead end; the fix must live in app.py.

**Rule:** A confirmation only COUNTS when its latest same-instrument confirmation
timestamp is `>=` the latest same-direction BOS/CHOCH structure anchor present in
the active window (`cfg("STAGE_WINDOW_MIN")`). A bare presence check is almost
always true and makes the confirmation gate meaningless.

**Why:** Without temporal anchoring, the strict trade verdict's confirmation gate
(and the journal STAGE label) is satisfied on every bar regardless of real
structure, producing false READY/Trade-Ready states.

**How to apply:** Any code that consumes a confirmation signal (the strict gate,
mitigation `reaction_confirmed`, the WAIT "missing" list, `_confluences` /
`confirmation_candle` / Edge Score, and the `get_setup_stage` label) must derive it
from the after-structure comparison, not from `_has(...confirmation...)`. Use `>=`
(same-bar/equal-timestamp confirmations count); do NOT add pre-structure tolerance —
that reintroduces the noise. Alert record timestamps are arrival-time (`now_utc()`
at ingestion), which is reliable at 5-min granularity.

**Config aside (not the spam cause):** On the MGC chart, the indicator's Instrument
input was wrongly set to MNQ. Confirmations are instrument-prefixed; a misrouted
Instrument input mislabels which side gets the alert. Advise users to verify each
chart's Instrument input matches the chart.
