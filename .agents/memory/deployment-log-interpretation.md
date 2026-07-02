---
name: Deployment log interpretation (Reserved VM)
description: How to read this project's production logs — why Flask INFO shows as [Error], and why /api healthcheck 500s at boot are benign.
---

# Reading production deployment logs

Two recurring sources of false alarm when investigating this project's PROD logs
(`fetchDeploymentLogs`). Both look scary, neither is a defect.

## 1. Flask INFO/WARNING lines are tagged `[Error]`
The Flask webhook server (`:8000`, supervised inside the api-server prod VM) writes
its normal status output to **stderr**, and the deployment log collector labels
everything on stderr as `[Error]`. Lines like `VWAP auto-fetch`, `Heartbeat sent`,
`Volatility auto-fetch`, and the per-alert scoring are routine INFO — not errors.
**How to apply:** judge severity by the message text (`INFO:`/`WARNING:` prefix),
not by the `[Error]` label the collector prepends.

## 2. `healthcheck /api returned status 500` clustered at boot = cold-start, benign
**Why:** the deployment is a Reserved VM. On every (re)start there is a short window
(~10-15s) where the platform health probe hits the service root `/api` before the
Express server (`:8080`) has finished binding its port, so the edge returns 500.
Once Express is listening, probes recover and stay green.
**How to spot it:** all the 500 lines fall inside a single short timestamp window and
never recur; surrounding/after logs are steady 200s. `getDeploymentInfo` shows
`hasSuccessfulBuild: true`. That signature = transient boot blip, **not** a code bug.
**Distinguish from a real outage:** the in-app proxy returns **502** ("Webhook server
unreachable") only when Express is UP but Flask is DOWN. A 502 (not 500), or 500s that
persist/recur outside a boot window, is worth real investigation.
**Don't "fix" the boot 500s:** startup health is correctly gated on `/api/healthz`
(Express-only, no Flask dependency); the platform retries until ready by design.

## 3. A cold-start 500 can occasionally FAIL the promote (re-publish, don't code-fix)
**Why:** the platform also runs a service-root readiness probe at `/api` (proxy →
Flask `/`). If the cold-start 500 window happens to exceed the startup-probe budget,
the **promote** fails even though the build phase succeeded — the build shows `failed`
and its (tiny, ~6-line) build log is stuck at `Waiting for deployment to be ready`,
with runtime logs retaining only a single `healthcheck /api returned status 500`.
**How to spot it:** the failed build differs from the previous *successful* build by
nothing runtime-relevant (e.g. only a screenshots/docs commit), and the prior build
keeps serving (`getDeploymentInfo` → `hasSuccessfulBuild: true`, prod endpoints 200).
**Fix:** just re-publish — it is transient infra timing, not a code bug. Confirmed:
one such failed promote was immediately followed by a green re-publish of effectively
the same tree. Only chase code if the 500s persist across multiple re-publishes or a
**502** appears (Express up, Flask down).

## 4. `Unrecognized alert type: 'X'` for a type the CURRENT registry covers = stale build
**Why:** the running deployment can lag the source. PROD logging
`WARNING: Unrecognized alert type: 'MES VOLUME SPIKE'` / `'MNQ BEARISH OB'` for an
instrument×suffix that the current `_PER_INSTRUMENT_ALERT_TEMPLATE` × `_ALERT_INSTRUMENTS`
build DOES cover means the live instance predates that add — NOT a code bug, and editing
the registry won't help.
**How to spot it:** reconstruct `ALERT_TYPES` from current source (exec the
`_ALERT_INSTRUMENTS` → `ALERT_TYPES.update(_SHARED_ALERT_TYPES)` block; ~152 entries) and
check membership; `git show <deployed-commit>:…app.py | grep _ALERT_INSTRUMENTS` for the
deployed side. If current source registers it → **re-publish**, don't touch the registry.
A scored `Alert: MES … → WAIT` line next to the "unrecognized" one is the diagnostic
HEARTBEAT re-eval off stored state — it does NOT mean the webhook itself was accepted.
