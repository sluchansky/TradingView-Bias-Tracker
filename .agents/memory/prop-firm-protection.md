---
name: Prop Firm Protection guard
description: Optional owner-only prop-rule guard in the webhook bot (app.py) — invariants any change to it must keep.
---

Optional "Prop Firm Protection: ON/OFF" guard layered on the TradingView webhook bot (`artifacts/tradingview-webhook/app.py`). It is a display layer + an account-manager + a money-path guard, all behind one in-memory toggle (default OFF, owner-only, resets OFF on restart).

**Invariants any change must keep:**
- OFF is a COMPLETE no-op: the guard must short-circuit BEFORE any DB/journal/news/active-trade read, so the four goldens (scalp, swing_flagoff, parity, instrument_isolation) stay byte-identical. Demoting only part of the OFF path leaks behavior.
- Guard runs as the FINAL layer inside `execute_trade_gateway`: AFTER `plan_public` is built, BEFORE the duplicate-send reservation, so it covers manual / auto / fast-entry / dual-TF orders alike.
- Enforce (409) ONLY on live modes (`execution_is_live`). manual_only/paper attach a "would-block" preview to the response but STILL return the plan.
- Fail-CLOSED: when ON and any required rule datum is missing/unclear → BLOCK (e.g. no active account → every live order blocked).
- `PROP_LOCK` guards ONLY in-memory state (toggle, active-account cache, decision deque); it must NEVER nest under `AUTO_TRADE_LOCK` / `SAFETY_LOCK` / `_TRADERSPOST_LOCK`. Copy-under-lock, then read DB/journal/news/trades outside the lock.
- Accounts persist in Postgres `prop_accounts` via INSERT/SELECT/UPDATE/DELETE + a boot readiness probe — NO in-app DDL (table created via the database tool in dev, publish schema-diff in prod), like the learning engine.
- Phase 1 rules only: allowed-instruments, max-contracts (per-order + aggregate open), daily-loss buffer (realized today + worst-case stop), conservative STATIC drawdown floor, trading-hours, news proximity (warn), overnight/weekend prohibition + flatten-before-close. Trailing/HWM drawdown is DEFERRED to Phase 2 and labeled "not armed until Phase 2" — never fake trailing protection.
- The bot has NO auto-flatten / broker exit executor (manual exits or broker TP/SL only). So the overnight/weekend rule must be enforced INDEPENDENT of the flatten-before-close window, and fail-CLOSED: restricted (allow_overnight=False, or allow_weekend=False on a Friday) + no flatten window set → BLOCK; restricted + past the daily close (mtc is None, the Globex evening session) → BLOCK; restricted + inside the flatten window → BLOCK; only intraday-outside-window passes. The original bug only blocked when `flatten_before_close_min > 0`, so disabling overnight holds with the window at 0 silently passed every order (false safety). Regression-guarded by `.local/state/check_prop_guard.sh` (run manually; not a registered workflow — the 10-workflow slot budget is full).
- Display surfaces (`/status` `prop_firm`, Main Brain `prop_rule` line on BOTH neutral+compute schemas, `#mod-prop` panel, `/prop-decisions` log) are display-only and NEVER gate; only the gateway guard blocks.
- Endpoints `/prop-protection` `/prop-accounts` `/prop-decisions` are owner-only (Express auth + flask-proxy whitelist; deliberately NOT in OPEN_PATHS).

**Why:** money-path safety + the bot's entire value rests on the goldens proving OFF == today's behavior; a leak here either silently changes live trading or blocks orders the user expected to go through.
