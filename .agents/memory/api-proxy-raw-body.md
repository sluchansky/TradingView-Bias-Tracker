---
name: Express /api proxy must forward the RAW body
description: Why the Express api-server proxy must buffer and forward the raw request body verbatim (not re-serialize req.body), or non-JSON webhooks (TradingView text/plain) are silently dropped before reaching Flask.
---

# Express /api proxy must forward the RAW body

The api-server Express proxy (`artifacts/api-server/src/`) forwards `/api/*` →
Flask `:8000`. It must buffer the request body as raw bytes (`express.raw({ type:
() => true })` in `app.ts`) and forward that Buffer **verbatim with the client's
original `content-type`**. It must NOT use `express.json()` and then re-serialize
`req.body`.

**Why:** `express.json()` only parses `Content-Type: application/json`. TradingView
posts webhook alerts as **`text/plain`** (its default). With `express.json()`,
`req.body` is `{}` for those, so a "forward only `JSON.stringify(req.body)`" proxy
sends an **empty body** to Flask → Flask logs `INCOMING POST /webhook | BODY:
<empty>` + `WARNING: Unrecognized alert type: ''` → the alert is dropped and the
dashboard shows **0 evaluations / "nothing being evaluated."** This presents as a
"published app is broken / I'm getting nothing" report even though Flask, Express,
healthchecks, VWAP/volatility are all healthy.

**How to apply / debug:**
- Symptom to look for in PROD or dev Flask logs: `BODY: <empty>` + `Unrecognized
  alert type: ''` on `POST /webhook` (real TradingView alerts arriving empty).
- Reproduce both content types against the dev domain and diff what Flask receives:
  `curl -X POST "$REPLIT_DEV_DOMAIN/api/webhook" -H "Content-Type: text/plain" --data "MNQ BEARISH SWEEP"`
  vs the same with `-H "Content-Type: application/json" --data '{"alert_type":...,"ticker":...}'`.
  Then `refresh_all_logs` (the `/tmp/logs/*.log` files are SNAPSHOTS, not live —
  grepping them right after a curl shows nothing until you refresh).
- Flask side already handles everything: `get_json(force=True, silent=True)` parses
  JSON regardless of content-type, and a raw-text fallback turns a bare string into
  `{"alert_type": <text>}`. The bug is never in Flask parsing — it's the proxy
  starving Flask of the body.
- `/webhook` is intentionally an OPEN path (TradingView can't send the dashboard
  password); auth lives in `dashboard-auth.ts` and does not read `req.body`, so a
  catch-all raw body parser is safe for the auth chain.

**Deploy note:** this fix is in the Express artifact, so production only gets it
after a **re-publish** — fixing dev does not fix the live site.
