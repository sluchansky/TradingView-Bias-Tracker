---
name: Dashboard ENTER -> execution gateway
description: When/how the dashboard ENTER button executes a trade (live broker / paper / manual) via the single configurable gateway, and the invariants that keep it money-safe.
---

The dashboard ENTER LONG/SHORT button drives ONE configurable execution gateway by
reusing the audited `/traderspost` route (route name kept for back-compat / no Express
auth change). The frontend has NO money-moving code of its own; it orchestrates the
route. Behavior is chosen server-side by `EXECUTION_MODE`
(manual_only | paper | traderspost | pickmytrade), surfaced to the UI via `/status`
fields `execution_mode`, `execution_live`, `execution_enabled`, `execution_provider_label`.

**Rule:** the button is enabled (`gatewayEligible`) only when `execution_enabled` is
true, no manual price typed (`manual = !!(e||s||t1||t2)`), `lastRec` exists AND is for the
currently-selected tab (`recInst === sym`), and the READY direction matches the selected
side. The route returns a `status`: `sent` (live broker), `simulated` (paper), or
`manual_required` (manual_only). Order sequence for a real send: POST `/traderspost`
FIRST; record the local `/enter` trade only when `status==='sent'` (abort otherwise → no
phantom trade). paper/manual never contact a broker and never engage broker dedupe.

**Why:** `/traderspost` is the single authoritative money-gate (READY / market-open /
contract-cap / dedupe, fail-closed). Frontend eligibility is UX only — never the safety
boundary. Typed prices stay tracking-only because the route deliberately refuses
client-supplied prices.

**How to apply:** any change near ENTER must keep: (a) manual/typed entries never reach a
live broker; (b) the executed instrument = the selected tab `sym`, and `/enter` non-manual
resolves the same instrument via `full_analysis(ticker_override=...)`; (c)
send-before-track with abort-on-not-sent for live mode. Never add a parallel money path
that bypasses `/traderspost`; add new providers as adapters behind it.
