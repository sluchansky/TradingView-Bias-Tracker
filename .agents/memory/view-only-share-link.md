---
name: View-only share link
description: Express-only watch-only dashboard link; the no-referrer vs Origin:null CSRF trap; the artifact.toml proxy-paths gotcha.
---

# View-only shareable dashboard link

A watch-only, expiring, password-protected link that lets non-owners VIEW the
live dashboard read-only. It is **Express-only** (Flask `app.py` left
byte-identical except one additive Share button). Lives entirely under `/view`
on the api-server. Stateless HMAC tokens (keys derived from DASHBOARD_PASSWORD);
the ONLY data path a viewer can reach is `GET /view/api/status` (upstream path
hardcoded to `/status`, only the query relayed) — every other method/path under
`/view/api` is 403 fail-closed.

## Gotcha 1 — a new Express route path must be added to the proxy `paths`
**Rule:** adding an `app.use("/newprefix", …)` in the api-server is NOT enough.
The top-level Replit path router forwards only the prefixes listed in
`artifacts/api-server/.replit-artifact/artifact.toml` `paths = [...]` to Express.
Any prefix not listed falls through to the static/`home` Vite SPA and you get
that app's HTML (with the Vite dev client) instead of your route — looks like the
route "doesn't exist" even though Express is correct.
**Why:** routing is decided at the proxy BEFORE Express; `/api` worked and `/view`
404'd purely because `/view` wasn't in `paths`.
**How to apply:** edit the toml via `verifyAndReplaceArtifactToml` (never in place),
add the prefix to `paths`, then restart the api-server workflow so the proxy
re-registers. `paths` governs prod deployment routing too.

## Gotcha 2 — `no-referrer` on a page that POSTs breaks the sameOrigin CSRF gate
**Rule:** a page served with `<meta name="referrer" content="no-referrer">` makes
browsers send `Origin: null` (and no Referer) on a same-origin form POST. A strict
CSRF check that requires `Origin`/`Referer` host == request host (our
`sameOrigin()`) then rejects EVERY legitimate submit with 403. Use
`content="same-origin"` on any page that POSTs to our own server; keep
`no-referrer` on pages whose URL carries a secret token (e.g. the `?t=<token>`
dashboard page) but that only issue GETs.
**Why:** curl masks this — an explicit `-H "Origin: …"` passes, so a curl-only test
gives false confidence. A real browser would have been locked out. Do NOT "fix" it
by making sameOrigin accept `null` — a cross-site attacker page under no-referrer
also sends `Origin: null`.
**How to apply:** test CSRF gates with curl sending (a) no Origin, (b) `Origin: null`,
(c) a cross-site Origin — all must 403 — and (d) the real same-origin Origin → pass.
For the actual browser behavior, verify the served page's referrer meta value.

## Residual security property (documented, accepted)
Link signatures are HMAC keys = unsalted `sha256(DASHBOARD_PASSWORD + ":" + label)`,
so any link holder has material for an OFFLINE dictionary attack on the admin
password. Acceptable only while DASHBOARD_PASSWORD is strong/high-entropy. Rotating
the admin password is the only revocation lever (invalidates all outstanding links).
