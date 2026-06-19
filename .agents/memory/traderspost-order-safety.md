---
name: Execution gateway live-order safety invariants
description: The one money-moving route (/traderspost, now a configurable execution gateway) and the safety rules any change to it must preserve.
---

# Execution gateway live-order safety invariants

`/traderspost` (POST, owner-only) is the **only** path in this app that can move real
money. It is a configurable execution gateway selected by `EXECUTION_MODE`
(manual_only | paper | traderspost | pickmytrade). A generic canonical "intent" is built
server-side, then a per-provider adapter renders the wire payload:
- `traderspost` → TradersPost → Tradovate (payload is **byte-equivalent** to the original
  TradersPost-only implementation; do not change its shape).
- `pickmytrade` → PickMyTrade (uses `EXECUTION_WEBHOOK_URL` + `EXECUTION_TOKEN` +
  `EXECUTION_ACCOUNT_ID`).
Default mode is `manual_only` unless a destination URL is set; in prod with
`TRADERSPOST_WEBHOOK_URL` present it resolves to `traderspost` (legacy behavior). Every
other `requests.post` in app.py targets Discord. Auth is the Express `/api` edge (NOT in
OPEN_PATHS, needs `DASHBOARD_PASSWORD` + same-origin, like `/enter`).

**Why this file exists:** the app was historically Discord/dashboard-only with NO broker
execution, so this is the single place where client trust, double-fire, or an ambiguous
network outcome turns into real financial loss. Treat these as hard invariants.

**Invariants any change MUST preserve:**
- **One money gate only.** Generalize this route in place; never add a parallel money
  path. New brokers = new adapters behind the same gate.
- **Live vs non-live split.** `paper` returns `simulated` and `manual_only` returns
  `manual_required` — both NEVER contact a broker and NEVER engage broker dedupe. Only
  live providers (`traderspost`/`pickmytrade`, configured with a URL) send + dedupe.
- **Server-authoritative pricing/direction/size.** Never trust client entry/stop/targets/
  direction. Resolve them ONLY from `full_analysis()` for the instrument. The client
  supplies just `ticker` + `contracts`.
- **Gate mirrors the UI exactly.** Send allowed only when `is_actionable(verdict)` AND
  `a.get("market_open") is not False` AND `trade_plan`/`entry_zone` present AND
  `ready_direction(verdict)` equals the plan's direction; else 409. (Market-closed can
  leave trade_plan populated while neutralizing the verdict — check both.)
- **Explicit instrument only.** `ticker` must resolve unambiguously to MGC or MNQ; a
  money-moving route never falls back to the active/default instrument.
- **Contracts capped server-side** 1..`TRADERSPOST_MAX_CONTRACTS` (env, default 10);
  non-int / out-of-range → 400.
- **Duplicate-send guard (live only).** A per-instrument fingerprint (direction + rounded
  entry/stop/first-target) is reserved under a lock BEFORE the POST; same fingerprint
  within `TRADERSPOST_COOLDOWN_SEC` (default 60) → 429.
- **Fail CLOSED on ambiguous outcomes.** ONLY a definite broker rejection (4xx) releases
  the dedupe slot to allow a retry. EVERY send-side exception (timeout, read error,
  connection reset, even refused) and every 5xx/3xx is ambiguous — the order may already
  be live — so the cooldown is HELD and the owner is told to verify at the broker
  (`broker_verify_required: true`, HTTP 502). Never auto-retry an ambiguous send.

## Data/confirmation alerts must NOT be pointed at a broker webhook
Broker webhooks accept only **JSON trade orders**, and in this app those come ONLY from
the dashboard ENTER button (`/traderspost`). Confirmation/data TradingView alerts (VOLUME
SPIKE, CVD, zone, structure) must go to the **APP** webhook
(`https://<app-domain>/api/webhook`) with a JSON `alert_type` message — never at the broker
strategy webhook. Symptom of a misroute: the broker rejects with a plain-text parse error
(e.g. `Unexpected 'V' ... Payload: Volume Crossing Up Threshold`) — that is a TradingView
alert misconfiguration, NOT an app/code bug. Fix = repoint that alert at the app webhook
with a JSON message, e.g. `{"alert_type":"MNQ VOLUME SPIKE","ticker":"{{ticker}}"}`.
