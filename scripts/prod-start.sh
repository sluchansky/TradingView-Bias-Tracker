#!/usr/bin/env bash
# Production supervisor for the TradingView webhook deployment.
#
# Runs the long-lived processes inside the single always-on VM:
#   1. Flask webhook server  -> internal, port 8000 (holds in-memory trade
#      state + background schedulers: heartbeat / EOD / weekly / VWAP fetch)
#   2. Express /api proxy     -> public entry, port 8080 (routes /api/* to Flask
#      on :8000 and /api2/* to the analysis bot on :8001)
#   3. Analysis-only bot      -> internal, port 8001 (dashboard-only mirror; see
#      its supervised launch below — isolated so it can't affect 1 or 2)
#
# If the Flask server OR the Express proxy exits, the whole script exits non-zero
# so the platform restarts the VM and brings both back up. This guarantees the
# webhook can never silently end up in a "proxy alive, Flask dead" state. The
# analysis bot is supervised separately and never triggers a VM restart.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[prod-start] launching Flask webhook server on :8000"
# DISCORD_LIVE=1 marks this as the single live sender so the time-based Discord
# schedulers (heartbeat / EOD / weekly / trade-ready) run here and NOT in the dev
# workspace, which shares the same Discord webhook secrets (avoids double alerts).
# ANALYSIS_BOT_FORWARD_URL mirrors every inbound /webhook to the analysis bot on
# :8001 (fire-and-forget; default-OFF when unset). It is set ONLY here in prod.
PORT=8000 DISCORD_LIVE=1 ANALYSIS_BOT_FORWARD_URL=http://localhost:8001/webhook PYTHONUNBUFFERED=1 .pythonlibs/bin/python3 artifacts/tradingview-webhook/app.py &
FLASK_PID=$!

echo "[prod-start] launching Express /api proxy on :8080"
PORT=8080 NODE_ENV=production node --enable-source-maps artifacts/api-server/dist/index.mjs &
EXPRESS_PID=$!

# ANALYSIS-ONLY bot (artifacts/analysis-bot) on :8001, reached publicly via the
# Express /api2 mount. ANALYSIS_ONLY=1 makes it dashboard-only: broker + Discord
# sends are suppressed and its DB access is confined to the `analysis_bot` schema.
# It runs in its OWN respawn loop and is deliberately NOT part of the `wait -n`
# below, so if this non-critical analyzer ever crashes it restarts on its own and
# can NEVER bounce (or otherwise affect) the live trading bot or the proxy.
echo "[prod-start] launching ANALYSIS-ONLY bot on :8001 (supervised; isolated from the live bot)"
(
  while true; do
    PORT=8001 ANALYSIS_ONLY=1 PYTHONUNBUFFERED=1 .pythonlibs/bin/python3 artifacts/analysis-bot/app.py
    echo "[prod-start] analysis bot exited (code $?); restarting in 5s" >&2
    sleep 5
  done
) &
ANALYSIS_SUP_PID=$!

shutdown() {
  echo "[prod-start] received signal, stopping children"
  kill "$FLASK_PID" "$EXPRESS_PID" "$ANALYSIS_SUP_PID" 2>/dev/null
  pkill -f "artifacts/analysis-bot/app.py" 2>/dev/null
  exit 0
}
trap shutdown SIGTERM SIGINT

# Block until whichever child exits first.
wait -n "$FLASK_PID" "$EXPRESS_PID"
EXIT_CODE=$?
echo "[prod-start] a child process exited (code $EXIT_CODE); stopping the other so the VM restarts both" >&2
kill "$FLASK_PID" "$EXPRESS_PID" 2>/dev/null
exit 1
