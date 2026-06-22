---
name: Dashboard "not updating" = quiet data, not a broken poll
description: How to diagnose a "dashboard doesn't update until I refresh" report on the trading app; it's usually stale inputs, not the 3s poll.
---

A report that the dashboard "doesn't update until I refresh" is almost always NOT a broken refresh loop.

**Rule:** The dashboard JS polls `/api/trade` + `/api/status` (and `/api/mode`) every 3s with silent `catch(e){}`. Before touching the timer, prove whether the poll runs.

**How to diagnose:**
- The user is on PROD (custom domain). Dev workflow logs only show the 5-min uptime `HEAD /` probe — they will NOT show the user's polling. Use `fetch_deployment_logs` for prod browser activity.
- If prod logs show `/api/status` and `/api/trade` returning **200 every ~3s**, the poll + auth are healthy. The freeze is perceptual.
- Confirm by polling prod `/api/status?ticker=MGC|MNQ` a few times and diffing fields.

**Why it looks frozen:** the inputs are quiet/stale, not the page.
- `current_price` only changes when a TradingView alert arrives. If none are coming in it sits frozen; tell-tale sign = price sitting far from the live auto VWAP (e.g. MNQ price ~30281 vs VWAP ~30619).
- VWAP / volatility are auto-fetched only ~once/min, so they barely move within a few seconds.
- `verdict` stays WAIT until a new structure alert (CHOCH/BOS) arrives (see no-signals-alert-config-gap).

**Fix shipped:** a visible "Last updated HH:MM:SS" clock (`markUpdated()` stamps local time on each successful poll; `checkStale()` turns it amber "⚠ Not responding" after >12s). This lets the user distinguish a dead poll from merely quiet data, and is the right thing to point at next time.

**The real lever** when data is genuinely stale: make sure the user's TradingView alerts (price + CHOCH/BOS structure) are actually reaching the live webhook — that's what moves the numbers, not the refresh interval.

**Third cause — "the MNQ oscillator/dial/probability meter doesn't move":** same family — a *correct* WAIT, not a broken gauge. Both the rec-dial and the "Trade Probability Meter" needle read `alert_diagnostics.edge_score`; the render JS is fully instrument-agnostic (no MNQ branch) and the 3s poll's `refreshRec()` fetches `/status?ticker=<selected tab>`, so the tab shows its own instrument. The needle parks at 0% whenever `edge_score==0`, which happens on `Struct: Undefined` (zone broke → no clean BOS/CHOCH → `edge=NN<threshold`). MNQ hits this far more than MGC (volatile Nasdaq: zone-mitigated re-arms constantly → confirmations "scoring skipped"; ATR ratio often 2–3.7×). **Why:** edge_score is a setup-strength value, not a continuous momentum oscillator, so it sits at 0 during no-structure stretches and looks dead. **How to apply:** before suspecting a render bug, pull `fetch_deployment_logs` `Alert: <SYM>` and confirm the instrument DID reach READY (Edge 70–85, `Gate: READY`) earlier — that proves the gauge moves; current 0% is just the live `Struct: Undefined` state. Don't "fix" it by touching the gate (money path).

**Second cause — stale CACHED dashboard, looks like "toggles don't update":** the dashboard is HTML with inline JS that changes every deploy. If it isn't served `no-store`, a browser can run an OLD copy (e.g. from before the pair/direction toggle→refetch wiring existed) and look frozen on MGC/MNQ or Long/Short switch while the server is fine.

**Rule — set AND propagate no-store at the PUBLIC boundary.** The browser loads the proxied `/api/dashboard` (Express), not Flask-direct `/dashboard`. The Express `/api` proxy forwards a header allow-list, so Flask's `Cache-Control`/`Pragma`/`Expires` must be explicitly forwarded (or set on the Express side) — otherwise no-store on Flask is invisible to the browser. **Why:** verifying only `curl localhost:8000/dashboard` (Flask) is a false positive; the proxy can strip the header. **How to apply:** when a correct-looking toggle/poll "isn't updating" and `/status?ticker=` hits the server every 3s in logs, suspect a cached dashboard first — verify with `curl -D - .../api/dashboard` (the proxied path) that no-store is present, then have the user hard-refresh.
