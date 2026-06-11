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
