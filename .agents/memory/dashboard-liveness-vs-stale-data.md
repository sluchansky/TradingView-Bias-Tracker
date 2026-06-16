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
