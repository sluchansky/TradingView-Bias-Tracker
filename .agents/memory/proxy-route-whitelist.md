---
name: api-server proxy route whitelist
description: Flask routes must be added to the Express proxy whitelist or they 404 before reaching Flask
---

# api-server proxy whitelist gotcha

The TradingView webhook (Flask, port 8000) is **not** exposed directly. It sits behind
the Node/Express `api-server` (the artifact mounted at `/api`). The proxy uses a
**hardcoded route whitelist** in `artifacts/api-server/src/routes/flask-proxy.ts`
(`router.all([ ... ], proxyToFlask)`).

**Rule:** Every Flask route you add in `app.py` must ALSO be added to that whitelist
array, or requests to `/api/<route>` return **404 from Express before they ever reach
Flask**. The router is mounted at `/api`, so inside the router `req.path` is the path
*without* the `/api` prefix (e.g. `/api/ping` → match `/ping`, `/api/` → match `/`).

**Why:** This caused confusing 404s — the path looks correct, the Flask route exists,
but the proxy silently drops it. Easy to waste time debugging Flask when the problem is
one missing string in the proxy.

**How to apply:** When adding/renaming a Flask endpoint, edit both files in lockstep.
After changing `flask-proxy.ts`, restart the `artifacts/api-server: API Server`
workflow (it rebuilds on start).

## Debugging 404s on this stack
- Two log sources: Flask `INCOMING <method> <path>` lines (a `@app.before_request` hook
  in `app.py`) AND the api-server pino logs. A wrong-path request (e.g. `/webhook`
  without `/api`) 404s at **Express** and never reaches Flask — so it only shows in the
  pino log, not the Flask INCOMING log. Check both.
- Unrecognized `alert_type` returns **200** `{"status":"ignored"}`, never 404. So a 404
  is always a routing/URL/proxy problem, never alert-processing logic.
- When external alerts (TradingView) 404 but local `curl` to `/api/webhook` returns 200,
  the failing alerts are pointing at a wrong/old URL on the sender side, not a server bug.

## Feeding new data inputs without touching the whitelist
To push a NEW kind of data (e.g. VWAP) from TradingView or the dashboard without adding a
Flask route + proxy-whitelist entry, reuse the already-whitelisted `/webhook` via a
**data-only alert type**: register it in `ALERT_TYPES` with `side:"data"`, add it to the
`_DATA_ONLY_TYPES` set, and short-circuit in `webhook()` AFTER price/VWAP ingestion but
BEFORE the `_COMMAND_TYPES` check / `ALERT_HISTORY` append — returning a small ack.
**Why:** keeps the change to one file (no proxy edit, no restart of api-server) and the
early return keeps the data push out of scoring, zone logic, history, and `_active_ticker`.
**How to apply:** `side:"data"` is inert — SUPPLY/DEMAND_TYPES only match bullish/bearish,
score_alerts only sees ALERT_HISTORY (which data-only pushes never enter). Send the
instrument either in `ticker` (`MGC1!`) or the alert-name prefix; `instrument_of()` resolves
both. Note `CURRENT_PRICE` is a single global shared by both instruments — an optional price
on a VWAP push for the non-active instrument will shift the active one's analysis price.
