---
name: Dashboard auth edge & open paths
description: Where dashboard/trade auth must live, which paths must never be locked, and the CSRF/host-trust model.
---

# Dashboard auth edge & open paths

**Rule:** Authentication for the dashboard and trade/mutation endpoints is enforced
in the Express api-server (the `/api` edge), NOT in Flask.

**Why:** the Flask proxy forwards only `content-type` and strips `Cookie` /
`Authorization` on the request and `Set-Cookie` on the response. Any cookie- or
header-based auth done inside Flask cannot survive the proxy. Express is the real
edge that sees the browser's headers, so the password gate (HTTP Basic Auth) lives
there as middleware inserted between the health router and the Flask proxy.

**Open paths that must NEVER be locked** (locking any breaks production):
- `/` — deployment healthcheck + service index (must return 200 or the deploy is
  marked unhealthy).
- `/ping` — uptime monitoring (e.g. UptimeRobot).
- `/webhook` — TradingView alert delivery.
- `/healthz` — Express health (stays open because it's mounted before auth).

**How to apply:** when adding any new endpoint or changing auth, keep these paths
open and put auth before the Flask proxy. Password comes from `DASHBOARD_PASSWORD`;
username is ignored; compared with `crypto.timingSafeEqual`. Fails OPEN only when
`NODE_ENV==='development'`; in production/deployment a missing secret fails CLOSED
(503) — a live trading dashboard must lock, not expose, when unconfigured.

**CSRF / host trust model:** mutating (non GET/HEAD/OPTIONS) protected requests must
be same-origin — the request `Origin`/`Referer` host must match a proxy-supplied
host. Verified empirically through the public proxy: Replit OVERWRITES any
client-supplied `X-Forwarded-Host` with the real public host, and a forged `Host`
is rejected upstream (502). So BOTH `x-forwarded-host` and `Host` are
proxy-controlled and unspoofable by a page; the check accepts a match against either
(robust to whichever carries the public host in dev vs prod, custom domain included,
with no hardcoding). The dashboard's own `fetch()` POSTs send `Origin` and pass.

**Manual ENTER must use the protected `/enter`, not `/webhook`:** the dashboard ENTER
button historically POSTed to the OPEN `/webhook` (so manual entry was unauthenticated).
It now posts to the protected `/enter` route, which is behaviorally equivalent to the
`/webhook` ENTER command path for manual entry. VWAP-set still posts to open `/webhook`
(reference value, intentionally out of the enter/close/mode scope).

**Webhook trade commands are OPEN (by explicit user choice):** the owner DOES use
TradingView to send `MGC/MNQ ENTER` and `MGC/MNQ CLOSE`, so the open `/webhook` wires
`_COMMAND_TYPES` straight to `_handle_command_alert` (no auth). This path is
intentionally unauthenticated — anyone who knows the webhook URL can trigger
ENTER/CLOSE. The user was shown the shared-secret-token alternative and declined it
in favour of keeping existing TradingView alerts working unmodified. **Why it can't be
password-protected:** TradingView alerts can only set URL + message body — no custom
HTTP header — so HTTP Basic Auth is impossible on this path; the only real lock is a
shared secret embedded in the alert JSON body. The dashboard ENTER button still posts
to the protected `/enter` (human path stays locked); only the machine/TradingView path
is open. **Reminder:** "entering a trade" via ENTER/CLOSE only records ACTIVE_TRADE
in-memory + posts Discord — it is NOT broker execution. The ONE real money-moving path
is the owner-only `/traderspost` route (→ TradersPost → Tradovate); see
`traderspost-order-safety.md` for its invariants. It is protected at the Express edge
(not in OPEN_PATHS) exactly like `/enter`.

**Visually testing the dashboard is blocked through the public proxy when
`DASHBOARD_PASSWORD` is set.** The Express fail-OPEN only triggers when the secret is
UNSET; with it set (the normal case), `/api/dashboard` and every protected path return
401 even in dev — so Playwright/screenshot smokes that route through the public proxy
cannot reach the dashboard without embedding the password (don't). **How to apply:**
verify dashboard changes against Flask DIRECTLY (`curl localhost:8000/dashboard` +
`/status?ticker=…`) and syntax-check the rendered inline JS (`node --check` on the
extracted `<script>` blocks) instead of bypassing auth.
