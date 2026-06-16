---
name: Production deployment topology
description: How this Flask webhook + Express proxy monorepo is deployed to production (target, who starts Flask, Python deps), and the constraints behind it.
---

# Production deployment topology

This project deploys as a **single Reserved VM** running BOTH processes together, with the
Express `/api` proxy as the public entry and Flask reached internally on `localhost:8000`.

- **Target must be VM, never autoscale.** Flask holds in-memory state (`ACTIVE_TRADE`, VWAP/analysis caches) and runs background schedulers (heartbeat, EOD 21:00 UTC, weekly Fri, VWAP fetch). Autoscale scales to zero / multi-replica → schedulers stop or duplicate and state is lost.
- **Flask is NOT a registered artifact.** It has no `artifact.toml`; in dev it runs only as a `.replit` workflow. So under `router="application"`, production would never start it on its own. Solution: the **api-server artifact's production `run` launches Flask too** via `scripts/prod-start.sh` (supervisor: starts Flask on PORT=8000 + Express on PORT=8080; if either exits the script exits non-zero so the VM restarts both — prevents a "proxy alive, Flask dead" steady state). SIGTERM exits 0 (no restart loop on intentional stop).
- **Python deps install in the api-server production *build*** (`scripts/prod-build.sh` = `uv sync --frozen` into `.pythonlibs` + `pnpm build`). The deploy image has no venv otherwise; `.pythonlibs` is gitignored. `uv` honors `UV_PROJECT_ENVIRONMENT=.pythonlibs`.
- **Startup health probe is `/api/healthz` (Express-direct), intentionally NOT proxied to Flask.** Keeps the probe a fast "entry is up" signal and avoids boot races (Flask boots slower). Flask liveness is handled by the supervisor, not the probe.

**Why these live in api-server's `[services.production]`:** `.replit` cannot be edited directly (platform-owned) and there is no exposed `deployConfig()` callback in code-execution. `verifyAndReplaceArtifactToml` is the only writable surface for prod build/run, so both the build (uv sync) and run (supervisor) hang off the one real deployable artifact.

**How to apply / gotchas:**
- The `deploymentTarget` (Reserved VM) is **chosen by the user in the Publish dialog** — the agent cannot set it (root `.replit` still says autoscale and can't be edited). Always tell the user to pick Reserved VM.
- The flask-proxy hardcodes `localhost:8000`, so Flask must bind 8000 in prod (supervisor sets `PORT=8000` inline for the python process; Express uses the `run.env` `PORT=8080`).
- Production needs the Discord webhook secrets present in the deployment environment (same names as dev).
- `wait -n` needs bash ≥4.3 (env has 5.2).
