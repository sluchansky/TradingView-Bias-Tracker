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
