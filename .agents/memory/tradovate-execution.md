---
name: Tradovate live execution
description: Safety invariants and design decisions for the live broker execution path in the TradingView webhook app (tradovate.py + app.py).
---

# Tradovate live order execution

Live broker execution was added to `artifacts/tradingview-webhook` as a separate
`tradovate.py` module (direct Tradovate REST, no SDK, only `requests`). The app
remains a tracking-only journal unless execution is explicitly turned on.

## Non-negotiable safety invariants (any change must preserve these)

- **The public `/webhook` must never auto-fire a real order.** Broker calls are
  gated behind `_broker_should_execute(data)` in app.py: fires only when
  `tv.execution_on()` AND (matching `exec_secret` when `TRADOVATE_EXEC_SECRET`
  is set, else `data.get("manual")` truthy). Raw TradingView alerts carry
  neither field, so they can reach scoring/journaling but never `place_bracket`
  / `flatten` / `move_stop_to_breakeven`.
  **Why:** the webhook URL is public; auto-trading from an unauthenticated POST
  is the worst-case failure for this app.
- **Execution is OFF by default and env defaults to DEMO.** The runtime toggle
  (`set_execution`) is in-memory only and reverts to the env default on restart;
  it refuses to enable without credentials.
- **A broker rejection must never read as success.** Order-placing call sites
  return HTTP 502 on failure. `tv.*` functions return `{"ok": False, ...}`
  structurally; `ok:True` is only returned after the broker confirms.
- **Partial fills keep ACTIVE_TRADE** (so the operator can flatten) but still
  return 502. The kept quantity must be the *actually placed* qty — `place_bracket`'s
  error return includes `contracts = sum of placed leg qtys`, not the requested count.
- **flatten must verify cancel responses.** `_cancel_working` returns
  `(cancelled, failed)`; `flatten` returns `ok:False` if any working-order cancel
  failed. **Why:** a surviving working stop/limit on a now-flat position can
  trigger and open a brand-new unintended position — silently counting a failed
  cancel as success is a real money-losing bug.
- **Never log secrets.** `_redact()` masks exec_secret/password/sec/cid/token/
  accessToken/deviceId in the before_request body log; tradovate.py never logs
  the auth request body.

## Design choices

- Market entry + OCO bracket via `/order/placeoso`; two-target split = ceil→T1,
  floor→T2 (`_split_legs`). Single contract → one bracket to T1.
- Tick rounding required (Tradovate rejects off-tick): MNQ 0.25, MGC 0.1 (`_TICK`).
- `max_contracts()` clamps size (default 2).
- Front-month resolved via `/contract/suggest` with a TTL cache + maturity parse.
- Auth token cached (~80 min) with locks and p-ticket/p-captcha backoff.
- Dashboard polls `/broker/status`; the live self-test only runs on first use or
  when forced (`/broker/test`) to avoid a broker round-trip every poll.

## Go-live caveat (surface to user before enabling)

When `TRADOVATE_EXEC_SECRET` is unset, the gate is just `manual:true`, which
anyone who knows the public `/api/webhook` URL could spoof; `/broker/toggle` is
also unauthenticated. Inert until the 6 Tradovate secrets exist, but
`TRADOVATE_EXEC_SECRET` should be treated as effectively required before turning
live execution on. (Secret comparison is also non-constant-time — minor.)
