---
name: View-only share link
description: Express-only watch-only dashboard link; the no-referrer vs Origin:null CSRF trap; the artifact.toml proxy-paths gotcha.
---

# View-only shareable dashboard link

Two access modes both live under `/view` on the api-server:
1. **Open (no-password):** `GET /view` with no `?t=` param → dashboard served
   immediately, no auth, no cookie. `/view/api/status` also requires no session
   cookie — intentionally open (read-only data). Share button copies the bare
   `/view` URL.
2. **Token-based (legacy):** `GET /view?t=<token>` → existing password-protected
   expiring flow (still works unchanged).

The ONLY data path any viewer can reach is `GET /view/api/status` (upstream path
hardcoded to `/status`, only the query relayed) — every other method/path under
`/view/api` is 403 fail-closed. Flask `app.py` has one additive Share button.

**JS escape trap in dashboard strings:** `\n` (single backslash-n) in a Python
triple-quoted string = raw newline at render time → JS single-quoted string
syntax error (node-check catches it). Either use `\\n` in Python source (→ `\n`
in file → newline char in runtime string → STILL a raw newline in the served JS)
OR simply avoid escape sequences entirely. The safe rule: use no `\n`/`\t`/`\r`
in dashboard JS string literals; use space or em-dash separators instead.

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

## Gotcha 3 — in-app browsers send NEITHER Origin NOR Referer (strict CSRF locks them out)
**Rule:** setting referrer-policy `same-origin` (Gotcha 2) is necessary but NOT
sufficient. Facebook Messenger / Instagram / other WKWebView in-app browsers omit
BOTH `Origin` and `Referer` on a same-origin top-level form POST regardless of the
referrer meta. A strict `sameOrigin()` (which needs one of them present) then 403s
every legitimate viewer ("Cross-origin request rejected", blank page). For a
LOW-VALUE POST like a read-only login (success only sets a harmless view cookie —
no trades, no state, no owner data), relax to "verify origin only if present":
`const src = origin ?? referer; if (src && !sameOrigin(req)) reject;`. A real
cross-site attack (evil.com) ALWAYS carries its own `Origin`, so this still blocks
it; only the header-less case is allowed through. Do NOT apply this relaxation to
the money-path `dashboard-auth.ts` gate — that stays strict.
**Why:** the symptom appeared only on the custom domain in Messenger; curl with an
explicit Origin passed, hiding it. Probe method: the CSRF check runs BEFORE token
verification, so POST a DUMMY token to prod and read the status — 403 = origin
rejected, 200 (login/expired page) = origin accepted — no valid token needed.
**How to apply:** when a browser-only auth POST "keeps getting rejected" but curl
works, curl the endpoint with NO Origin and NO Referer at all; if that 403s, an
in-app browser will too.

## Residual security property (documented, accepted)
Link signatures are HMAC keys = unsalted `sha256(DASHBOARD_PASSWORD + ":" + label)`,
so any link holder has material for an OFFLINE dictionary attack on the admin
password. Acceptable only while DASHBOARD_PASSWORD is strong/high-entropy. Rotating
the admin password is the only revocation lever (invalidates all outstanding links).
