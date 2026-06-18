---
name: Dashboard ENTER -> real broker order
description: When/how the dashboard ENTER button places a REAL Tradovate order vs tracking-only, and the invariants that keep it money-safe.
---

The dashboard ENTER LONG/SHORT button can place a REAL broker order by reusing the
audited `/traderspost` route (TradersPost -> Tradovate). It has NO money-moving
code of its own; it orchestrates the existing route from the frontend.

**Rule:** a real order fires only when the frontend `liveEligible` is true — no
manual price typed (`manual = !!(e||s||t1||t2)`), `lastRec` exists AND is for the
currently-selected tab (`recInst === sym`), `traderspost_configured`, and the
READY direction matches the selected side. Otherwise ENTER is tracking-only.
Order sequence: POST `/traderspost` FIRST; record the local `/enter` trade only if
the broker returns `status==='sent'` (abort on anything else → no phantom trade).

**Why:** `/traderspost` is the single authoritative money-gate (READY / market-open
/ contract-cap / dedupe, fail-closed). Frontend `liveEligible` is UX only — never
the safety boundary. Typed prices must stay tracking-only because the broker route
deliberately refuses client-supplied prices.

**How to apply:** any change near ENTER must keep: (a) manual/typed entries never
reach the broker; (b) the broker instrument = the selected tab `sym`, and `/enter`
non-manual resolves the same instrument via `full_analysis(ticker_override=...)` so
the tracked plan matches what was sent; (c) broker-send-before-track ordering with
abort-on-not-sent. Never add a parallel money path that bypasses `/traderspost`.
