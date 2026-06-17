#!/usr/bin/env bash
# Production supervisor for the TradingView webhook deployment.
#
# Runs BOTH long-lived processes inside the single always-on VM:
#   1. Flask webhook server  -> internal, port 8000 (holds in-memory trade
#      state + background schedulers: heartbeat / EOD / weekly / VWAP fetch)
#   2. Express /api proxy     -> public entry, port 8080 (routes /api/* to Flask)
#
# If EITHER process exits, the whole script exits non-zero so the platform
# restarts the VM and brings both back up. This guarantees the webhook can
# never silently end up in a "proxy alive, Flask dead" state.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[prod-start] launching Flask webhook server on :8000"
# DISCORD_LIVE=1 marks this as the single live sender so the time-based Discord
# schedulers (heartbeat / EOD / weekly / trade-ready) run here and NOT in the dev
# workspace, which shares the same Discord webhook secrets (avoids double alerts).
PORT=8000 DISCORD_LIVE=1 PYTHONUNBUFFERED=1 .pythonlibs/bin/python3 artifacts/tradingview-webhook/app.py &
FLASK_PID=$!

echo "[prod-start] launching Express /api proxy on :8080"
PORT=8080 NODE_ENV=production node --enable-source-maps artifacts/api-server/dist/index.mjs &
EXPRESS_PID=$!

shutdown() {
  echo "[prod-start] received signal, stopping children"
  kill "$FLASK_PID" "$EXPRESS_PID" 2>/dev/null
  exit 0
}
trap shutdown SIGTERM SIGINT

# Block until whichever child exits first.
wait -n "$FLASK_PID" "$EXPRESS_PID"
EXIT_CODE=$?
echo "[prod-start] a child process exited (code $EXIT_CODE); stopping the other so the VM restarts both" >&2
kill "$FLASK_PID" "$EXPRESS_PID" 2>/dev/null
exit 1
