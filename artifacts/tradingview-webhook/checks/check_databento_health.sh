#!/usr/bin/env bash
# check_databento_health.sh — V1-P2-001: Databento health smoke
#
# Verifies ARCH §5 Scenario 1 and AC-7.2:
#   (1) The platform correctly reports the Databento feed as OFFLINE in dev.
#       Guard: (not DATABENTO_ENABLED) OR (_DATABENTO_BRAIN is None).
#   (2) full_analysis() still produces valid gate evaluations while OFFLINE.
#   (3) Expert interface contract (_version=v1, guaranteed fields) intact.
#
# READ-ONLY. No app.py changes. No live Databento key required.
# No secrets. No network calls. No broker communication.
# Acceptance: exits 0 when all assertions pass; exits 1 on any failure.

set -euo pipefail

# Resolve repository root from this script's own location — portable regardless of CWD.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "${SCRIPT_DIR}/../../.." && pwd)
APP_DIR="${REPO_DIR}/artifacts/tradingview-webhook"

# Prefer the Replit-managed interpreter; fall back to whatever python3 is on PATH.
PY="${REPLIT_PYTHON:-$(command -v python3)}"

cd "${REPO_DIR}"

"${PY}" - <<PYEOF
import sys, json
sys.path.insert(0, "${APP_DIR}")
import app

# T1: /databento-status endpoint reports OFFLINE (enabled=False, ok=False).
# In dev: DATABENTO_ENABLED may be set but _DATABENTO_BRAIN is always None
# (only populated in __main__ with a valid API key). Use Flask test client
# so jsonify() has the required application context.
with app.app.test_client() as client:
    resp = client.get("/databento-status")
    data = resp.get_json()
assert resp.status_code == 200, (
    "T1 FAIL: expected HTTP 200 from /databento-status; got %d" % resp.status_code)
assert data.get("enabled") is False, (
    "T1 FAIL: expected enabled=False when _DATABENTO_BRAIN is None; got enabled=%r"
    % data.get("enabled"))
assert data.get("ok") is False, (
    "T1 FAIL: expected ok=False when OFFLINE; got ok=%r" % data.get("ok"))
print("  PASS  T1: /databento-status -> enabled=False, ok=False  (reason=%r)"
      % data.get("reason"))

# T2: full_analysis() runs without error with Databento OFFLINE (gate continuity).
result = app.full_analysis()
assert isinstance(result, dict), "T2 FAIL: full_analysis() must return a dict"
assert "verdict" in result, \
    "T2 FAIL: full_analysis() must have 'verdict' key with Databento OFFLINE"
assert "_version" in result, "T2 FAIL: full_analysis() must carry _version field"
print("  PASS  T2: full_analysis() runs with Databento OFFLINE  (verdict=%r)"
      % result.get("verdict"))

# T3: Expert interface contract unaffected by Databento OFFLINE.
assert result.get("_version") == "v1", \
    "T3 FAIL: Expert _version must be v1; got %r" % result.get("_version")
for field in ("verdict", "edge_score", "strict_reason", "gate_debug", "_version"):
    assert field in result, "T3 FAIL: Expert guaranteed field missing: %r" % field
print("  PASS  T3: Expert interface contract intact with Databento OFFLINE")

# T4: OFFLINE guard logic fires correctly.
brain_is_none = app._DATABENTO_BRAIN is None
enabled = bool(app.DATABENTO_ENABLED)
gate_fires = (not enabled) or brain_is_none
assert gate_fires, \
    "T4 FAIL: OFFLINE guard must fire; enabled=%r, brain_is_none=%r" % (enabled, brain_is_none)
print("  PASS  T4: OFFLINE guard correct  (enabled=%r, brain_is_none=%r)"
      % (enabled, brain_is_none))

print("")
print("DATABENTO HEALTH SMOKE OK")
print("(OFFLINE detection + gate continuity + interface contract confirmed)")
PYEOF
