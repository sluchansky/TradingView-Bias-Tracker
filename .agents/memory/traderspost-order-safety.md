---
name: TradersPost live-order safety invariants
description: The one money-moving route (/traderspost → TradersPost → Tradovate) and the safety rules any change to it must preserve.
---

# TradersPost live-order safety invariants

`/traderspost` (POST, owner-only) is the **only** path in this app that moves real
money: it sends a MARKET order + protective-stop/first-target bracket to TradersPost,
which bridges to the broker (Tradovate for Apex/Tradeify). Every other `requests.post`
in app.py targets Discord. Auth is the Express `/api` edge (NOT in OPEN_PATHS, needs
`DASHBOARD_PASSWORD` + same-origin, like `/enter`).

**Why this file exists:** the app was historically Discord/dashboard-only with NO
broker execution, so this is the single place where client trust, double-fire, or an
ambiguous network outcome turns into real financial loss. Treat these as hard
invariants, not preferences.

**Invariants any change MUST preserve:**
- **Server-authoritative pricing/direction.** Never trust client-supplied
  entry/stop/targets/direction. Resolve them ONLY from `full_analysis()` for the
  instrument. The client supplies just `ticker` + `contracts`.
- **Gate mirrors the UI exactly.** Send is allowed only when `is_actionable(verdict)`
  AND `a.get("market_open") is not False` AND `trade_plan`/`entry_zone` present AND
  `ready_direction(verdict)` equals the plan's direction; otherwise 409. An
  authenticated POST must never be able to send when the dashboard button wouldn't
  show (market-closed can leave trade_plan populated while neutralizing the verdict —
  check both).
- **Explicit instrument only.** `ticker` must resolve unambiguously to MGC or MNQ
  (mutually-exclusive substring); a money-moving route never falls back to the
  active/default instrument.
- **Contracts capped server-side** 1..`TRADERSPOST_MAX_CONTRACTS` (env, default 10);
  the browser min/step is not a control. Non-int or out-of-range → 400.
- **Duplicate-send guard.** A per-instrument fingerprint (direction + rounded
  entry/stop/first-target) is reserved under a lock BEFORE the POST; the same
  fingerprint again within the cooldown window returns 429. Cap and cooldown are env
  knobs (`TRADERSPOST_MAX_CONTRACTS` default 10, `TRADERSPOST_COOLDOWN_SEC` default 60).
- **Fail CLOSED on ambiguous outcomes.** ONLY a definite broker rejection (4xx)
  releases the dedupe slot to allow a retry. EVERY send-side exception (timeout, read
  error, connection reset, even refused) and every 5xx/3xx is ambiguous — the order
  may already be live — so the cooldown is HELD and the owner is told to verify at the
  broker. Never auto-retry an ambiguous broker send. **Why:** a `ConnectionError` can
  be a reset AFTER TradersPost received the order, so "connection failed" is not proof
  of "no order placed."

## Data/confirmation alerts must NOT be pointed at TradersPost
TradersPost only accepts **JSON trade orders**, and in this app those come ONLY from the
dashboard ENTER button (`/traderspost`, always valid JSON). Confirmation/data TradingView
alerts (VOLUME SPIKE, CVD, zone, structure) must be pointed at the **APP** webhook
(`https://<app-domain>/api/webhook`) with a JSON `alert_type` message — never at the
TradersPost strategy webhook.
**Symptom of a misroute:** TradersPost rejects with `Unexpected 'V' at line 1 column 1 of the
JSON5 data — Payload: Volume Crossing Up Threshold (Red)` (or similar plain text). That plain
text is a TradingView indicator's DEFAULT message hitting the TradersPost webhook URL — it is a
TradingView alert misconfiguration, NOT an app/code bug (the app never sends non-JSON to
TradersPost). Fix = repoint that alert at the app webhook and set its message to JSON, e.g.
`{"alert_type":"MNQ VOLUME SPIKE","ticker":"{{ticker}}"}` (prefixed type self-resolves the
instrument; `{{ticker}}` is harmless extra).
