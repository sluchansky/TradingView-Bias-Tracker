---
name: Dashboard auth edge & open paths
description: Where dashboard/trade auth must live and which paths must never be locked.
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

**How to apply:** when adding any new endpoint or changing auth, keep these four
paths open and put auth before the Flask proxy. Password comes from the
`DASHBOARD_PASSWORD` secret; username is ignored; compared with
`crypto.timingSafeEqual`; fails OPEN with a warning when the secret is unset so the
owner is never locked out.

**Known gap:** Basic Auth has no CSRF protection on mutating endpoints
(`/mode`, `/enter`, `/close`, etc.). Browsers can attach cached Basic credentials
to cross-site requests. This becomes financially material once live broker order
execution exists — add Origin/Referer (vs `x-forwarded-host`) validation or a CSRF
token for non-GET protected requests before that point.
