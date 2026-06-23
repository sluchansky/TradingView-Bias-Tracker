---
name: Dashboard media via inlined data URI
description: How to add audio/image assets to the Flask operator dashboard without a new route — base64 data URI injected into the HTML.
---

# Dashboard media via inlined data URI

To add a media asset (audio clip, image) to the operator dashboard, base64-encode
it once at import into a `data:` URI constant and inject it into the dashboard HTML
with `html.replace("__TOKEN__", DATA_URI)` — the same mechanism the dashboard
already uses for `__EDGE_MAX__`. Do NOT serve it from a new Flask route.

**Why:** the dashboard is reached through the Express `/api` proxy and is
auth-gated by `dashboardAuth`. A new asset route would have to be added to the
proxy whitelist AND to the auth OPEN_PATHS, and binary streaming through the proxy
is an extra failure surface. Inlining travels inside the already-working
`/dashboard` HTML response, so it needs none of that. base64 / data-URI characters
contain no `"`, `\`, or newline, so the literal is safe both in the triple-quoted
Python HTML string and in a double-quoted JS string (avoids the
dashboard-js-string-escape-bug class of breakage).

**How to apply:** loader resolves the asset via `__file__`-relative path (repo
ships as one Reserved VM, so `../../attached_assets/...` works in dev and prod),
fail-open to `""` if missing so a missing asset never crashes boot or the page.
Play audio through the EXISTING dashboard AudioContext (decodeAudioData) so the
existing pointerdown/keydown autoplay-unlock primes it; keep an HTMLAudio element
as a fallback. Trade-taken trigger = ACTIVE_TRADE `opened_at` transition seen in
the `/trade` poll, with a one-time baseline flag so a trade already live on page
load does not ring. Size note: a ~6s mp3 ≈ 73KB → ~97KB base64; fine inline.
