---
name: TradersPost webhook connectivity probe
description: How to safely verify the TRADERSPOST_WEBHOOK_URL secret actually authenticates, and how to read the failure modes.
---

# TradersPost webhook connectivity probe

When the user changes brokers / TradersPost accounts they hand over a new webhook URL of the form
`https://webhooks.traderspost.io/trading/webhook/{webhook-id}/{password}`. The **last segment is a password**;
a wrong/stale/truncated last segment is the usual breakage ("INVALID PASSWORD" in the TradersPost UI).

## Safe no-trade probe
The app's `/traderspost` send path issues REAL market orders (resolve_execution_mode → "traderspost" whenever
the secret is set and no EXECUTION_MODE override). There is NO built-in dry run. To test connectivity WITHOUT
placing an order, POST a payload that omits `action` and `ticker` — TradersPost can't build an order from it.

## How to read the result (authoritative)
- **HTTP 400 `{"messageCode":"invalid-payload", "...action and ticker fields are required"}`** = password is
  **VALID**. Auth passed; it only rejected the deliberately-incomplete test payload. This is the success signal.
- **`invalid-password` body** = the last URL segment is wrong → user must copy the full URL again or regenerate it.
- **HTTP 000 from curl** = transient network blip OR (common) the secret has a **trailing newline/space** from the
  paste. curl chokes on whitespace in the URL; the APP does not (it `.strip()`s the value), so 000 from a raw
  curl probe is NOT proof the app is broken.

**Why:** a stray newline made `len_raw` one byte longer than `len_trim`; raw probe = HTTP 000, but the stripped
value probed HTTP 400 invalid-payload (valid). The app uses the stripped form, so it worked fine.

## Stale boot-snapshot gotcha
The `<available_secrets>` list in the session boot snapshot can be STALE — it once listed
`TRADERSPOST_WEBHOOK_URL` (and `DISCORD_BOT_TOKEN`) that were actually **absent** at runtime.
A missing `TRADERSPOST_WEBHOOK_URL` makes `resolve_execution_mode` silently fall back to
`manual_only` (no live orders anywhere), which looks identical to "bot won't execute" bugs.
Always confirm secret existence with `viewEnvVars({type:"secret"})` before assuming it's set;
never trust the snapshot. Secrets can only be saved via `requestEnvVar` (or the Secrets tab),
never `setEnvVars`. After saving, restart dev + **republish** so the live deployment rebinds it.

## Regenerate/rotate nuance
Regenerating a TradersPost webhook rotates ONLY the password (last URL segment); the webhook-id
(2nd-to-last segment) stays the same. Observed: after a rotate, the OLD url STILL probed HTTP 400
invalid-payload (i.e. looked valid) — likely a grace/propagation window or the old secret not being
hard-revoked. So the no-order probe canNOT prove an old exposed URL is dead. Confirm revocation in the
TradersPost UI, not via the probe. To tell "same vs different URL saved" without printing the secret:
compare `$TRADERSPOST_WEBHOOK_URL` (stripped) to the known literal and diff the `awk -F/ '{print $(NF-1)}'` id segment.

## How to apply
- Probe the **stripped** value (`tr -d '[:space:]'`) — that's what the app sees — and expect HTTP 400 invalid-payload.
- Never print the secret. Redact the URL out of any body with `sed "s#$URL#[REDACTED]#g"`.
- Host reachability sanity: `curl https://webhooks.traderspost.io/` returns **404** (reachable; root has no route).
- Secrets can only be set via requestEnvVar (can't set directly). After saving, restart "TradingView Webhook Server".
- Dev & prod SHARE secrets, so this same URL drives the live published instance once deployed.
