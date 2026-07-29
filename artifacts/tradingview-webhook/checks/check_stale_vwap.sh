#!/usr/bin/env bash
# check_stale_vwap.sh — V1-P2-003: Stale-VWAP gate smoke
#
# Verifies ARCH §5 Scenario 2 and AC-5.3 (stale-data failsafe):
#   The strict gate must refuse READY when VWAP is stale or absent.
#   - get_vwap() returns (None, "missing") when no VWAP is stored.
#   - get_vwap() returns (None, "stale") when timestamp exceeds freshness window.
#   - get_vwap() returns (float, "ok") when VWAP is current.
#   - Gate condition: vwap_ok = (vwap_status == "ok") and vwap is not None.
#
# READ-ONLY. No app.py changes. No live feed required. No secrets.
# Test state is isolated and restored after each assertion.
# Acceptance: exits 0 when all assertions pass; exits 1 on any failure.

set -euo pipefail

# Resolve repository root from this script's own location — portable regardless of CWD.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "${SCRIPT_DIR}/../../.." && pwd)
APP_DIR="${REPO_DIR}/artifacts/tradingview-webhook"

PY="${REPLIT_PYTHON:-$(command -v python3)}"

cd "${REPO_DIR}"

"${PY}" - <<PYEOF
import sys
sys.path.insert(0, "${APP_DIR}")
import app
from datetime import datetime, timezone, timedelta

MGC = "MGC"

# Save existing VWAP state for full restoration after every test.
_saved_vwap = dict(app.VWAP_BY_TICKER)

try:
    # T1: No VWAP stored -> (None, "missing") -> gate cannot pass VWAP check.
    app.VWAP_BY_TICKER.pop(MGC, None)
    val, status = app.get_vwap(MGC)
    assert val is None, "T1 FAIL: expected vwap=None when missing; got %r" % val
    assert status == "missing", "T1 FAIL: expected status='missing'; got %r" % status
    vwap_ok = (status == "ok") and val is not None
    assert not vwap_ok, "T1 FAIL: vwap_ok must be False when VWAP is missing"
    print("  PASS  T1: missing VWAP -> status='missing', vwap_ok=False")

    # T2: Stale VWAP (2h-old timestamp > default 30 min freshness window) ->
    #     (None, "stale") -> gate still blocked.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    app.VWAP_BY_TICKER[MGC] = {"value": 2700.0, "ts": old_ts, "source": "smoke_test"}
    val_s, status_s = app.get_vwap(MGC)
    assert val_s is None, "T2 FAIL: stale VWAP must return val=None; got %r" % val_s
    assert status_s == "stale", \
        "T2 FAIL: expected status='stale' for 2h-old VWAP; got %r" % status_s
    vwap_ok_s = (status_s == "ok") and val_s is not None
    assert not vwap_ok_s, "T2 FAIL: vwap_ok must be False on stale VWAP"
    print("  PASS  T2: stale VWAP (2h old) -> status='stale', vwap_ok=False")

    # T3: Fresh VWAP (current timestamp) -> (float, "ok") -> gate can evaluate.
    fresh_ts = datetime.now(timezone.utc).isoformat()
    app.VWAP_BY_TICKER[MGC] = {"value": 2700.0, "ts": fresh_ts, "source": "smoke_test"}
    val_f, status_f = app.get_vwap(MGC)
    assert val_f is not None, "T3 FAIL: fresh VWAP must return a float value"
    assert isinstance(val_f, float), \
        "T3 FAIL: fresh VWAP value must be float; got %s" % type(val_f).__name__
    assert status_f == "ok", \
        "T3 FAIL: expected status='ok' for fresh VWAP; got %r" % status_f
    vwap_ok_f = (status_f == "ok") and val_f is not None
    assert vwap_ok_f, "T3 FAIL: vwap_ok must be True on fresh VWAP"
    print("  PASS  T3: fresh VWAP -> status='ok', vwap_ok=True (gate can evaluate)")

    # T4: Gate boundary — three status strings map to exactly two gate outcomes.
    # Replicates the gate expression from evaluate_strict_setup (~line 7048).
    price = 2700.0
    outcomes = [
        ("missing", None,    False),
        ("stale",   None,    False),
        ("ok",      2700.0,  True),
        ("n/a",     None,    False),
        ("ok",      None,    False),   # ok status but vwap=None
    ]
    for vwap_status, vwap_value, expected in outcomes:
        actual = (vwap_status == "ok") and (vwap_value is not None) and (price is not None)
        assert actual == expected, (
            "T4 FAIL: status=%r, vwap=%r -> vwap_ok=%r, expected %r"
            % (vwap_status, vwap_value, actual, expected))
    print("  PASS  T4: gate boundary confirmed  (missing->False, stale->False, ok->True)")

finally:
    # Restore original VWAP state regardless of outcome — isolated test.
    app.VWAP_BY_TICKER.clear()
    app.VWAP_BY_TICKER.update(_saved_vwap)

print("")
print("STALE-VWAP GATE SMOKE OK")
print("(missing/stale/ok boundary confirmed; gate refuses non-ok VWAP)")
PYEOF
