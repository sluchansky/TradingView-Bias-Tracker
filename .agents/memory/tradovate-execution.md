---
name: Tradovate live execution
description: Safety invariants and design decisions for the live broker execution path in the TradingView webhook app (tradovate.py + app.py).
---

# Tradovate live order execution

Live broker execution was added to `artifacts/tradingview-webhook` as a separate
`tradovate.py` module (direct Tradovate REST, no SDK, only `requests`). The app
remains a tracking-only journal unless execution is explicitly turned on.

## Non-negotiable safety invariants (any change must preserve these)

- **A configured `TRADOVATE_EXEC_SECRET` is MANDATORY for every broker action;
  there is no `manual:true` fallback.** Two gates in app.py:
  `_exec_authorized(data)` = secret configured AND `hmac.compare_digest` match
  (constant-time); `_broker_should_open(data)` = `tv.execution_on()` AND
  `_exec_authorized`. **OPEN** (ENTER, both sites) uses `_broker_should_open`.
  **Why:** the webhook URL is public; auto-trading from an unauthenticated POST
  is the worst-case failure. A raw TradingView alert carries no `exec_secret`,
  so it can reach scoring/journaling but never `place_bracket`.
- **CLOSE / breakeven on a broker-backed trade must actually execute at the
  broker before any local mutation.** Gate is `ACTIVE_TRADE.get("broker")`
  truthy → require `_exec_authorized` (NOT the toggle — a real position must
  always be closeable even after execution is toggled off) → call
  `tv.flatten` / `move_stop_to_breakeven` → 502 on failure → only then mutate
  local state. Non-broker-backed (tracking-only) trades stay local-only.
  **Why:** the old code mutated local state first, so a missing/wrong secret
  made the UI say "closed/BE" while the real position stayed live.
- **Execution is OFF by default and env defaults to DEMO.** The runtime toggle
  (`set_execution`) is in-memory only and reverts to the env default on restart;
  it refuses to enable without credentials AND without `TRADOVATE_EXEC_SECRET`
  configured. `/broker/toggle` requires a valid `exec_secret` to enable;
  disabling is always allowed (fail-safe direction).
- **A broker rejection must never read as success.** Order-placing call sites
  return HTTP 502 on failure. `tv.*` functions return `{"ok": False, ...}`
  structurally; `ok:True` is only returned after the broker confirms.
- **Partial fills keep ACTIVE_TRADE** (so the operator can flatten) but still
  return 502. The kept quantity must be the *actually placed* qty — `place_bracket`'s
  error return includes `contracts = sum of placed leg qtys`, not the requested count.
- **flatten must verify it can list AND cancel working orders.** `_cancel_working`
  returns `(cancelled, failed, list_err)`; `flatten` returns `ok:False` if it
  cannot even *list* working orders (`list_err`) OR if any cancel failed.
  **Why:** a surviving working stop/limit on a now-flat position can trigger and
  open a brand-new unintended position — counting either a list failure or a
  failed cancel as success is a real money-losing bug.
- **Position checks must fail closed.** `_position_qty` returns `(qty, err)`;
  on any `/position/list` error it returns `(None, err)` and `flatten` returns
  `ok:False`. **Why:** coercing an API error to qty 0 made a transient failure
  look like a flat account, so CLOSE skipped liquidation and reported success
  while a real position stayed open.
- **ENTER is a process-level critical section.** A module-level
  `_ENTER_LOCK` (threading.Lock) is held across duplicate-check → `place_bracket`
  → `ACTIVE_TRADE` set, in BOTH ENTER paths (command handler + `/enter`). The
  `if ACTIVE_TRADE: 409` check inside the lock is the dedup. **Why:** the bare
  check was outside any lock, so two concurrent ENTERs could both place orders
  before either set ACTIVE_TRADE. (Distinct from tradovate's `_order_lock`; no
  deadlock since they never nest the same lock.)
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

Inert until the 6 Tradovate secrets exist AND `TRADOVATE_EXEC_SECRET` is set.
Without the exec secret nothing can be enabled or fired (the gate is fail-closed:
no secret → `_exec_authorized` always False → toggle 403, broker never runs).
The dashboard always shows the exec-secret field and warns when the server has
no `TRADOVATE_EXEC_SECRET` configured (`status_snapshot.exec_secret_configured`).
