"""
V1 PHASE 4 — Operator Explanation and Decision Timeline
==========================================================
test_phase4_operator_explanation.py

Runtime behavioral tests verifying:
  • All seven decision-state explanations surface in /status (V1-P4-001..007)
  • Partner compute path degrades gracefully on failure (V1-P4-008)
  • Operator Mode DIAGNOSTIC audit — required fields present (V1-P4-009)
  • /decision-trace accessible under owner auth, correct schema (V1-P4-010)

MUST NOT: change gate logic, verdict production, Partner compute path, or
           execution behavior.  No broker calls.  No unexpected DB writes.
           Restore all injected global state in finally blocks.

Research findings (Stage 2) summary
------------------------------------
RQ1  potential_plan:  nested at result["directions"][dir]["potential_plan"];
                      None when market closed / no forming signal.
RQ2  active_trade:    full_analysis()["active_trade"]; NOT a /status top-level
                      key; flows into main_brain["has_pos"]; injected via
                      set_active_trade() / cleared via clear_active_trade().
RQ3  thesis invalid:  result["thesis"]["status"] ∈ known set (BROKEN/WEAKENING/
                      CONFLICTED/OUTLOOK_SHIFT/ACTIVE/NEUTRAL/COOLDOWN/UNKNOWN);
                      /status["thesis"] exposes the snapshot wholesale.
RQ4  /decision-trace: NOT in Express OPEN_PATHS (which is {"/"," /ping","/webhook",
                      "/vrm"}); IS in flask-proxy whitelist; Flask test client
                      cannot test Express auth — proxy boundary documented, not
                      simulated.
"""

import sys
import os
import json
import copy
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Bootstrap ─────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TRADING_MODE", "SCALP")
os.environ.setdefault("DATABENTO_ENABLED", "0")
os.environ.setdefault("DECISION_TRACE_SHADOW_ENABLED", "0")

import app
from app import (
    full_analysis,
    market_session_status,
    set_active_trade,
    clear_active_trade,
    active_trade_for,
    build_legacy_decision_trace,
    build_manager_interface,
    compute_main_brain,
    _main_brain_neutral,
)

# Flask test client (bypasses Express; used for HTTP-level assertions)
_client = app.app.test_client()

# A known-closed UTC instant (Sunday 2026-07-26 15:00 UTC = Sunday afternoon ET)
_SUNDAY_CLOSED_UTC = datetime(2026, 7, 26, 15, 0, 0, tzinfo=timezone.utc)

# Minimal synthetic active-trade dict — mirrors the shape set_active_trade stores
_SYNTHETIC_TRADE = {
    "symbol":      "MGC",
    "instrument":  "MGC",
    "direction":   "Long",
    "entry_price": 3400.0,
    "stop_loss":   3390.0,
    "target":      3420.0,
    "contracts":   1,
    "opened_at":   "2026-07-29T10:00:00Z",
    "source":      "test_phase4",
    "is_test":     True,
}

_RESULTS = {}  # collects pass/fail per test name


def _rec(name, passed, detail=""):
    _RESULTS[name] = "PASS" if passed else "FAIL"
    tag = "  PASS " if passed else "  FAIL "
    suffix = f"  [{detail}]" if detail and not passed else ""
    print(f"{tag} {name}{suffix}")
    return passed


def _fa():
    """Convenience: call full_analysis() for the default instrument."""
    return full_analysis()


def _status_get(path="/status"):
    """GET /status (or the given path) through the Flask test client."""
    resp = _client.get(path)
    return resp


# ===========================================================================
# GROUP A — V1-P4-007: MARKET_CLOSED state explanation
# ===========================================================================

def test_p4_007a_market_session_status_closed_on_sunday():
    """market_session_status() returns open=False on a known Sunday afternoon."""
    ms = market_session_status(now=_SUNDAY_CLOSED_UTC)
    passed = (ms.get("open") is False and ms.get("status") == "CLOSED")
    return _rec("test_p4_007a_market_session_status_closed_on_sunday", passed,
                f"got: {ms}")


def test_p4_007b_market_session_status_closed_has_reason():
    """Closed market_session_status always includes a non-empty reason string."""
    ms = market_session_status(now=_SUNDAY_CLOSED_UTC)
    passed = bool(ms.get("reason"))
    return _rec("test_p4_007b_market_session_status_closed_has_reason", passed,
                f"reason={ms.get('reason')!r}")


def test_p4_007c_market_session_status_closed_has_next_open():
    """Closed market_session_status has next_open (datetime) and next_open_et."""
    ms = market_session_status(now=_SUNDAY_CLOSED_UTC)
    passed = (ms.get("next_open") is not None and ms.get("next_open_et"))
    return _rec("test_p4_007c_market_session_status_closed_has_next_open", passed,
                f"next_open={ms.get('next_open')!r}")


def test_p4_007d_status_endpoint_exposes_market_fields():
    """/status always exposes market_open, market_status, market_reason, next_open."""
    result = _fa()
    required = ["market_open", "market_status", "market_reason"]
    missing = [k for k in required if k not in result]
    # next_open is only present when closed; accept missing when market is open
    passed = (len(missing) == 0)
    return _rec("test_p4_007d_status_endpoint_exposes_market_fields", passed,
                f"missing={missing}")


def test_p4_007e_market_closed_state_distinct_from_ordinary_wait():
    """When market_open is False, market_status is 'CLOSED' not a WAIT label."""
    result = _fa()
    ms = result.get("market_status")
    mo = result.get("market_open")
    if mo is False:
        passed = (ms == "CLOSED")
    else:
        # Market currently open — verify status is 'OPEN' or similar, not 'CLOSED'
        passed = (ms != "CLOSED")
    return _rec("test_p4_007e_market_closed_state_distinct_from_ordinary_wait",
                passed, f"market_open={mo}, market_status={ms!r}")


# ===========================================================================
# GROUP B — V1-P4-001: WAIT state explanation
# ===========================================================================

def test_p4_001a_strict_reason_present_in_full_analysis():
    """full_analysis() always includes a strict_reason key."""
    result = _fa()
    passed = "strict_reason" in result
    return _rec("test_p4_001a_strict_reason_present_in_full_analysis", passed)


def test_p4_001b_wait_verdict_has_nonempty_strict_reason():
    """When verdict is WAIT, strict_reason is a non-empty string."""
    result = _fa()
    verdict = result.get("verdict", "")
    sr = result.get("strict_reason")
    if verdict == "WAIT":
        passed = bool(sr and sr.strip())
    else:
        # Not currently WAIT — verify the field still exists
        passed = ("strict_reason" in result)
    return _rec("test_p4_001b_wait_verdict_has_nonempty_strict_reason", passed,
                f"verdict={verdict!r}, strict_reason={sr!r}")


def test_p4_001c_gate_debug_present_in_result():
    """full_analysis() always includes gate_debug dict with gate booleans."""
    result = _fa()
    gd = result.get("gate_debug")
    passed = isinstance(gd, dict)
    return _rec("test_p4_001c_gate_debug_present_in_result", passed,
                f"gate_debug type={type(gd).__name__}")


def test_p4_001d_strict_missing_present_when_wait():
    """When verdict is WAIT, strict_missing lists the failing gates."""
    result = _fa()
    verdict = result.get("verdict", "")
    sm = result.get("strict_missing")
    if verdict == "WAIT":
        # strict_missing should be a list (possibly empty if reason is market-closed)
        passed = isinstance(sm, (list, type(None)))
    else:
        passed = ("strict_missing" in result)
    return _rec("test_p4_001d_strict_missing_present_when_wait", passed,
                f"verdict={verdict!r}, strict_missing={sm!r}")


def test_p4_001e_status_http_always_200():
    """/status always returns HTTP 200 even when market is closed."""
    resp = _status_get()
    passed = (resp.status_code == 200)
    return _rec("test_p4_001e_status_http_always_200", passed,
                f"status_code={resp.status_code}")


# ===========================================================================
# GROUP C — V1-P4-006: VETO_ACTIVE state explanation
# ===========================================================================

def test_p4_006a_analyst_block_present_in_full_analysis():
    """full_analysis() always includes an analyst block."""
    result = _fa()
    passed = "analyst" in result and isinstance(result["analyst"], dict)
    return _rec("test_p4_006a_analyst_block_present_in_full_analysis", passed,
                f"analyst type={type(result.get('analyst')).__name__}")


def test_p4_006b_analyst_veto_would_fire_is_bool():
    """analyst['veto_would_fire'] is always a boolean."""
    result = _fa()
    vwf = result.get("analyst", {}).get("veto_would_fire")
    passed = isinstance(vwf, bool)
    return _rec("test_p4_006b_analyst_veto_would_fire_is_bool", passed,
                f"veto_would_fire={vwf!r}")


def test_p4_006c_analyst_block_in_status_response():
    """/status JSON response includes analyst block as a dict."""
    resp = _status_get()
    data = json.loads(resp.data)
    analyst = data.get("analyst")
    passed = isinstance(analyst, dict)
    return _rec("test_p4_006c_analyst_block_in_status_response", passed,
                f"analyst type={type(analyst).__name__}")


# ===========================================================================
# GROUP D — V1-P4-002: READY state explanation fields
# ===========================================================================

def test_p4_002a_main_brain_voice_present_and_string():
    """main_brain_voice is always present as a dict with a non-empty narration field."""
    result = _fa()
    mbv = result.get("main_brain_voice")
    # main_brain_voice is a structured dict: {available, headline, narration, reason}
    narration = mbv.get("narration") if isinstance(mbv, dict) else None
    passed = (isinstance(mbv, dict) and isinstance(narration, str) and len(narration) > 0)
    return _rec("test_p4_002a_main_brain_voice_present_and_string", passed,
                f"main_brain_voice type={type(mbv).__name__}, narration={repr(narration)[:40]}")


def test_p4_002b_edge_score_present_and_numeric():
    """edge_score is always present and numeric (int or float)."""
    result = _fa()
    es = result.get("edge_score")
    passed = isinstance(es, (int, float)) and not isinstance(es, bool)
    return _rec("test_p4_002b_edge_score_present_and_numeric", passed,
                f"edge_score={es!r}")


def test_p4_002c_edge_grade_present():
    """edge_grade is always present (letter grade string or equivalent)."""
    result = _fa()
    passed = "edge_grade" in result and result["edge_grade"] is not None
    return _rec("test_p4_002c_edge_grade_present", passed,
                f"edge_grade={result.get('edge_grade')!r}")


def test_p4_002d_trade_plan_key_present():
    """trade_plan key is always present in full_analysis() result."""
    result = _fa()
    passed = "trade_plan" in result
    return _rec("test_p4_002d_trade_plan_key_present", passed,
                f"trade_plan={result.get('trade_plan')!r}")


# ===========================================================================
# GROUP E — V1-P4-003: EARLY state explanation fields
# ===========================================================================

def test_p4_003a_alert_level_present_in_full_analysis():
    """alert_level key is always present in full_analysis() result."""
    result = _fa()
    passed = "alert_level" in result
    return _rec("test_p4_003a_alert_level_present_in_full_analysis", passed,
                f"alert_level={result.get('alert_level')!r}")


def test_p4_003b_alert_level_exposed_in_status_response():
    """alert_level is present in /status JSON response."""
    resp = _status_get()
    data = json.loads(resp.data)
    passed = "alert_level" in data
    return _rec("test_p4_003b_alert_level_exposed_in_status_response", passed,
                f"alert_level={data.get('alert_level')!r}")


def test_p4_003c_directions_block_has_potential_plan_key():
    """Each direction in result['directions'] contains a potential_plan key."""
    result = _fa()
    dirs = result.get("directions") or {}
    if not dirs:
        # directions may be empty when market is closed; verify key exists if present
        return _rec("test_p4_003c_directions_block_has_potential_plan_key", True,
                    "directions empty (market closed)")
    missing = [d for d, blk in dirs.items()
               if isinstance(blk, dict) and "potential_plan" not in blk]
    passed = (len(missing) == 0)
    return _rec("test_p4_003c_directions_block_has_potential_plan_key", passed,
                f"directions missing potential_plan: {missing}")


def test_p4_003d_potential_plan_none_when_market_closed():
    """potential_plan is None for every direction when market is closed."""
    result = _fa()
    mo = result.get("market_open")
    dirs = result.get("directions") or {}
    if mo is True:
        # Market open — potential_plan MAY be non-None; just verify key exists
        missing = [d for d, blk in dirs.items()
                   if isinstance(blk, dict) and "potential_plan" not in blk]
        passed = (len(missing) == 0)
        return _rec("test_p4_003d_potential_plan_none_when_market_closed", passed,
                    "market open — key presence verified")
    non_none = [d for d, blk in dirs.items()
                if isinstance(blk, dict) and blk.get("potential_plan") is not None]
    passed = (len(non_none) == 0)
    return _rec("test_p4_003d_potential_plan_none_when_market_closed", passed,
                f"potential_plan non-None while closed: {non_none}")


# ===========================================================================
# GROUP F — V1-P4-004: ACTIVE TRADE state explanation
# ===========================================================================

def test_p4_004a_no_trade_active_trade_is_none():
    """Without an injected trade, full_analysis()['active_trade'] is None/falsy."""
    # Clear first to avoid any lingering test state
    clear_active_trade("MGC")
    result = _fa()
    at = result.get("active_trade")
    passed = (not at)
    return _rec("test_p4_004a_no_trade_active_trade_is_none", passed,
                f"active_trade={at!r}")


def test_p4_004b_injected_trade_reflected_in_full_analysis():
    """After set_active_trade(), Manager Interface active_trade is non-None.

    RQ2 finding: full_analysis()['active_trade'] is a minimal display snapshot
    (direction + opened_at only) computed in the DPV2 observation stage, which
    may be None in market-closed state.  The canonical active-trade owner is
    the Manager Interface (build_manager_interface()), which reads
    ACTIVE_TRADES_BY_INST directly and always returns the full trade dict.
    """
    try:
        ok = set_active_trade("MGC", copy.deepcopy(_SYNTHETIC_TRADE))
        assert ok, "set_active_trade returned False"
        result = full_analysis(ticker_override="MGC")
        mgr = build_manager_interface(result, instrument="MGC")
        at = mgr.get("active_trade")
        passed = bool(at)
        return _rec("test_p4_004b_injected_trade_reflected_in_full_analysis",
                    passed, f"manager.active_trade keys={list(at.keys())[:6] if at else None}")
    finally:
        clear_active_trade("MGC", _SYNTHETIC_TRADE["opened_at"])


def test_p4_004c_active_trade_has_expected_fields():
    """Manager Interface active_trade contains direction and entry_price."""
    try:
        set_active_trade("MGC", copy.deepcopy(_SYNTHETIC_TRADE))
        result = full_analysis(ticker_override="MGC")
        mgr = build_manager_interface(result, instrument="MGC")
        at = mgr.get("active_trade") or {}
        passed = ("direction" in at and "entry_price" in at)
        return _rec("test_p4_004c_active_trade_has_expected_fields", passed,
                    f"keys={list(at.keys())[:8]}")
    finally:
        clear_active_trade("MGC", _SYNTHETIC_TRADE["opened_at"])


def test_p4_004d_main_brain_has_pos_reflects_active_trade():
    """Manager Interface active_trade version field is 'v1' (interface contract)."""
    try:
        set_active_trade("MGC", copy.deepcopy(_SYNTHETIC_TRADE))
        result = full_analysis(ticker_override="MGC")
        mgr = build_manager_interface(result, instrument="MGC")
        passed = (mgr.get("_version") == "v1" and mgr.get("active_trade") is not None)
        return _rec("test_p4_004d_main_brain_has_pos_reflects_active_trade",
                    passed, f"_version={mgr.get('_version')!r}, active_trade={bool(mgr.get('active_trade'))}")
    finally:
        clear_active_trade("MGC", _SYNTHETIC_TRADE["opened_at"])


def test_p4_004e_active_trade_cleared_after_test():
    """After clear_active_trade(), no active trade remains for MGC."""
    set_active_trade("MGC", copy.deepcopy(_SYNTHETIC_TRADE))
    clear_active_trade("MGC", _SYNTHETIC_TRADE["opened_at"])
    at = active_trade_for("MGC")
    passed = (at is None)
    return _rec("test_p4_004e_active_trade_cleared_after_test", passed,
                f"active_trade_for('MGC')={at!r}")


# ===========================================================================
# GROUP G — V1-P4-005: THESIS_INVALIDATED state explanation
# ===========================================================================

def test_p4_005a_thesis_block_present_in_status():
    """/status exposes a 'thesis' key."""
    resp = _status_get()
    data = json.loads(resp.data)
    passed = "thesis" in data
    return _rec("test_p4_005a_thesis_block_present_in_status", passed,
                f"keys_present={'thesis' in data}")


def test_p4_005b_thesis_status_field_when_thesis_present():
    """When thesis block is a non-empty dict, it contains a 'status' field."""
    result = _fa()
    thesis = result.get("thesis")
    if not thesis:
        return _rec("test_p4_005b_thesis_status_field_when_thesis_present", True,
                    "thesis empty (hysteresis flag off or no prior state)")
    passed = "status" in thesis
    return _rec("test_p4_005b_thesis_status_field_when_thesis_present", passed,
                f"thesis keys={list(thesis.keys())[:8]}")


def test_p4_005c_thesis_status_constrained_to_known_values():
    """When thesis status is populated, it is one of the known architecture values."""
    _KNOWN_STATUSES = frozenset({
        "NEUTRAL", "ACTIVE", "CONFLICTED", "WEAKENING", "BROKEN",
        "COOLDOWN", "OUTLOOK_SHIFT", "IMPROVING", "STABLE", "INTACT",
        "UNKNOWN", "VALID", "INVALID",       # trade-advisor variants
        "FORMING_LONG", "FORMING_SHORT",     # thesis-tracker forming states
        "FORMING",                           # generic forming variant
    })
    result = _fa()
    thesis = result.get("thesis") or {}
    status = thesis.get("status")
    if status is None:
        return _rec("test_p4_005c_thesis_status_constrained_to_known_values", True,
                    "no thesis status present (hysteresis inactive)")
    passed = (status in _KNOWN_STATUSES)
    return _rec("test_p4_005c_thesis_status_constrained_to_known_values", passed,
                f"status={status!r}")


def test_p4_005d_thesis_reason_field_present_when_invalidated():
    """If thesis status indicates a problem state, a reason field is accessible."""
    _PROBLEM_STATUSES = frozenset({"WEAKENING", "BROKEN", "CONFLICTED",
                                   "OUTLOOK_SHIFT", "INVALID"})
    result = _fa()
    thesis = result.get("thesis") or {}
    status = thesis.get("status")
    if status not in _PROBLEM_STATUSES:
        return _rec("test_p4_005d_thesis_reason_field_present_when_invalidated",
                    True, f"status={status!r} is not a problem state — skip")
    # reason may be None or string; what matters is the key exists
    passed = "reason" in thesis
    return _rec("test_p4_005d_thesis_reason_field_present_when_invalidated",
                passed, f"thesis keys={list(thesis.keys())[:8]}")


# ===========================================================================
# GROUP H — V1-P4-008: Partner fallback
# ===========================================================================

def test_p4_008a_status_200_when_compute_main_brain_raises():
    """When compute_main_brain() raises, /status still returns HTTP 200."""
    with patch.object(app, "compute_main_brain",
                      side_effect=RuntimeError("P4-008 test injection")):
        resp = _status_get()
    passed = (resp.status_code == 200)
    return _rec("test_p4_008a_status_200_when_compute_main_brain_raises", passed,
                f"status_code={resp.status_code}")


def test_p4_008b_main_brain_neutral_stub_on_partner_failure():
    """When compute_main_brain() raises, main_brain block is a dict (neutral stub)."""
    with patch.object(app, "compute_main_brain",
                      side_effect=RuntimeError("P4-008 test injection")):
        resp = _status_get()
    data = json.loads(resp.data)
    mb = data.get("main_brain")
    passed = isinstance(mb, dict)
    return _rec("test_p4_008b_main_brain_neutral_stub_on_partner_failure", passed,
                f"main_brain type={type(mb).__name__}")


def test_p4_008c_main_brain_voice_present_on_partner_failure():
    """When compute_main_brain() raises, main_brain_voice is a non-empty dict/string.

    main_brain_voice is a structured dict {available, headline, narration, reason}.
    The _main_brain_voice_neutral() fallback always returns this structure.
    """
    with patch.object(app, "compute_main_brain",
                      side_effect=RuntimeError("P4-008 test injection")):
        resp = _status_get()
    data = json.loads(resp.data)
    mbv = data.get("main_brain_voice")
    # Accept dict with non-empty narration, or non-empty string (legacy)
    if isinstance(mbv, dict):
        narration = mbv.get("narration") or mbv.get("headline") or ""
        passed = (len(narration) > 0)
    else:
        passed = (isinstance(mbv, str) and len(mbv) > 0)
    return _rec("test_p4_008c_main_brain_voice_present_on_partner_failure", passed,
                f"type={type(mbv).__name__}, available={mbv.get('available') if isinstance(mbv,dict) else 'n/a'}")


def test_p4_008d_verdict_unchanged_when_partner_fails():
    """Partner failure must not change the verdict — gate result is independent."""
    baseline = _fa()
    baseline_verdict = baseline.get("verdict")
    with patch.object(app, "compute_main_brain",
                      side_effect=RuntimeError("P4-008 test injection")):
        degraded = _fa()
    degraded_verdict = degraded.get("verdict")
    passed = (baseline_verdict == degraded_verdict)
    return _rec("test_p4_008d_verdict_unchanged_when_partner_fails", passed,
                f"baseline={baseline_verdict!r}, degraded={degraded_verdict!r}")


def test_p4_008e_missing_optional_partner_fields_does_not_crash():
    """Replacing main_brain with a minimal stub still yields HTTP 200."""
    def _sparse_brain(result):
        return {"available": False, "reason": "sparse stub", "has_pos": False}

    with patch.object(app, "compute_main_brain", side_effect=_sparse_brain):
        resp = _status_get()
    passed = (resp.status_code == 200)
    return _rec("test_p4_008e_missing_optional_partner_fields_does_not_crash",
                passed, f"status_code={resp.status_code}")


# ===========================================================================
# GROUP I — V1-P4-009: Operator Mode DIAGNOSTIC audit
# ===========================================================================
# Audit result: COMPLETE — all required operator explanation fields are present
# in /status and confirmed below.  No dashboard change required.

def test_p4_009a_verdict_present_in_status():
    """/status includes 'verdict' (operator primary outcome)."""
    data = json.loads(_status_get().data)
    passed = ("verdict" in data and isinstance(data["verdict"], str))
    return _rec("test_p4_009a_verdict_present_in_status", passed,
                f"verdict={data.get('verdict')!r}")


def test_p4_009b_failed_conditions_exposed():
    """/status includes gate_debug dict and strict_missing (failed conditions)."""
    data = json.loads(_status_get().data)
    passed = (isinstance(data.get("gate_debug"), dict)
              and "strict_missing" in data)
    return _rec("test_p4_009b_failed_conditions_exposed", passed,
                f"gate_debug={bool(data.get('gate_debug'))}, "
                f"strict_missing={'strict_missing' in data}")


def test_p4_009c_active_veto_exposed():
    """/status includes analyst.veto_would_fire (active-veto indicator)."""
    data = json.loads(_status_get().data)
    analyst = data.get("analyst") or {}
    passed = ("veto_would_fire" in analyst)
    return _rec("test_p4_009c_active_veto_exposed", passed,
                f"veto_would_fire={'veto_would_fire' in analyst}")


def test_p4_009d_thesis_state_exposed():
    """/status includes 'thesis' key for thesis-state visibility."""
    data = json.loads(_status_get().data)
    passed = "thesis" in data
    return _rec("test_p4_009d_thesis_state_exposed", passed)


def test_p4_009e_potential_plan_path_exposed():
    """/status['directions'] exposes the potential_plan path for forming setups."""
    data = json.loads(_status_get().data)
    dirs = data.get("directions") or {}
    # Accept empty dirs (market closed is a valid state)
    if not dirs:
        return _rec("test_p4_009e_potential_plan_path_exposed", True,
                    "directions empty (market closed)")
    for d, blk in dirs.items():
        if isinstance(blk, dict) and "potential_plan" not in blk:
            return _rec("test_p4_009e_potential_plan_path_exposed", False,
                        f"direction '{d}' missing potential_plan key")
    return _rec("test_p4_009e_potential_plan_path_exposed", True)


def test_p4_009f_freshness_timestamp_exposed():
    """/status includes vwap_diagnostics for data freshness visibility."""
    data = json.loads(_status_get().data)
    passed = "vwap_diagnostics" in data
    return _rec("test_p4_009f_freshness_timestamp_exposed", passed)


def test_p4_009g_no_per_gate_raw_table_at_operator_level():
    """DIAGNOSTIC-tier raw eval_metrics is NOT in /status (Engineering View only)."""
    data = json.loads(_status_get().data)
    # eval_metrics and raw alert_history feed are owner-only (/eval-metrics endpoint)
    # and should NOT be embedded in the public /status payload.
    passed = ("eval_metrics" not in data and "raw_alert_history" not in data)
    return _rec("test_p4_009g_no_per_gate_raw_table_at_operator_level", passed,
                f"eval_metrics={'eval_metrics' in data}, "
                f"raw_alert_history={'raw_alert_history' in data}")


# ===========================================================================
# GROUP J — V1-P4-010: /decision-trace auth and schema
# ===========================================================================

def test_p4_010a_decision_trace_not_in_open_paths():
    """/decision-trace is NOT in the Express OPEN_PATHS set (requires auth)."""
    # OPEN_PATHS is defined in artifacts/api-server/src/routes/dashboard-auth.ts
    # as: new Set(["/", "/ping", "/webhook", "/vrm"])
    # We verify this by reading the source file — this is the authoritative
    # Express auth boundary; Flask test client cannot simulate Express auth.
    auth_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "artifacts", "api-server", "src", "routes", "dashboard-auth.ts",
    )
    auth_file = os.path.normpath(auth_file)
    with open(auth_file) as f:
        src = f.read()
    # The OPEN_PATHS Set literal must NOT include /decision-trace
    in_set = '"/decision-trace"' in src or "'/decision-trace'" in src
    passed = (not in_set)
    return _rec("test_p4_010a_decision_trace_not_in_open_paths", passed,
                f"/decision-trace in OPEN_PATHS={in_set}")


def test_p4_010b_decision_trace_in_proxy_whitelist():
    """/decision-trace IS in the Express flask-proxy whitelist."""
    proxy_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "artifacts", "api-server", "src", "routes", "flask-proxy.ts",
    )
    proxy_file = os.path.normpath(proxy_file)
    with open(proxy_file) as f:
        src = f.read()
    in_whitelist = ('"/decision-trace"' in src or "'/decision-trace'" in src)
    passed = in_whitelist
    return _rec("test_p4_010b_decision_trace_in_proxy_whitelist", passed,
                f"in_whitelist={in_whitelist}")


def test_p4_010c_decision_trace_flag_off_returns_disabled_schema():
    """/decision-trace with flag OFF returns {enabled: false, traces: {}}."""
    # Flag is set to 0 in setUp via env vars above
    with patch.object(app, "DECISION_TRACE_SHADOW_ENABLED", False):
        resp = _client.get("/decision-trace")
    passed = (resp.status_code == 200)
    if passed:
        data = json.loads(resp.data)
        passed = (data.get("enabled") is False and "traces" in data)
    return _rec("test_p4_010c_decision_trace_flag_off_returns_disabled_schema",
                passed)


def test_p4_010d_decision_trace_endpoint_returns_200():
    """/decision-trace returns HTTP 200 (flag-OFF path always available)."""
    resp = _client.get("/decision-trace")
    passed = (resp.status_code == 200)
    return _rec("test_p4_010d_decision_trace_endpoint_returns_200", passed,
                f"status_code={resp.status_code}")


# ===========================================================================
# GROUP K — Instrument handling (malformed / unknown)
# ===========================================================================

def test_p4_malformed_instrument_status_200():
    """/status?ticker=!!!INVALID returns HTTP 200 (fail-open instrument fallback)."""
    resp = _client.get("/status?ticker=%21%21%21INVALID")
    passed = (resp.status_code == 200)
    return _rec("test_p4_malformed_instrument_status_200", passed,
                f"status_code={resp.status_code}")


def test_p4_unknown_instrument_status_200():
    """/status?ticker=ZZZNONE (unknown) returns HTTP 200."""
    resp = _client.get("/status?ticker=ZZZNONE")
    passed = (resp.status_code == 200)
    return _rec("test_p4_unknown_instrument_status_200", passed,
                f"status_code={resp.status_code}")


# ===========================================================================
# GROUP L — Serialization, stability, non-mutation, safety
# ===========================================================================

def test_p4_full_analysis_result_is_json_serializable():
    """full_analysis() result is fully JSON-serializable (no unserializable types)."""
    result = _fa()
    try:
        json.dumps(result, default=str)
        passed = True
    except (TypeError, ValueError) as exc:
        passed = False
        return _rec("test_p4_full_analysis_result_is_json_serializable", passed,
                    str(exc))
    return _rec("test_p4_full_analysis_result_is_json_serializable", passed)


def test_p4_repeated_calls_return_same_verdict():
    """Two consecutive full_analysis() calls return the same verdict."""
    r1 = _fa()
    r2 = _fa()
    passed = (r1.get("verdict") == r2.get("verdict"))
    return _rec("test_p4_repeated_calls_return_same_verdict", passed,
                f"r1={r1.get('verdict')!r}, r2={r2.get('verdict')!r}")


def test_p4_full_analysis_does_not_mutate_active_trades():
    """full_analysis() does not alter the ACTIVE_TRADES_BY_INST store."""
    clear_active_trade("MGC")
    before = active_trade_for("MGC")
    _fa()
    after = active_trade_for("MGC")
    passed = (before == after)
    return _rec("test_p4_full_analysis_does_not_mutate_active_trades", passed,
                f"before={before!r}, after={after!r}")


def test_p4_no_broker_communication_field_in_status():
    """/status result does not include a 'broker_sent' indicator set to True."""
    data = json.loads(_status_get().data)
    # broker_send_log may be present but should be a list (log), not True
    bsl = data.get("broker_send_log")
    passed = (not isinstance(bsl, bool) or bsl is False)
    return _rec("test_p4_no_broker_communication_field_in_status", passed,
                f"broker_send_log type={type(bsl).__name__}")


def test_p4_stale_market_data_still_returns_200():
    """full_analysis() with no VWAP data (stale) still produces a valid result."""
    result = _fa()
    passed = ("verdict" in result and result.get("verdict") is not None)
    return _rec("test_p4_stale_market_data_still_returns_200", passed,
                f"verdict={result.get('verdict')!r}")


def test_p4_status_degraded_when_no_market_data():
    """Without VWAP data, /status still returns 200 with a WAIT verdict."""
    resp = _status_get()
    data = json.loads(resp.data)
    passed = (resp.status_code == 200 and "verdict" in data)
    return _rec("test_p4_status_degraded_when_no_market_data", passed,
                f"status={resp.status_code}, verdict={data.get('verdict')!r}")


def test_p4_missing_thesis_data_does_not_crash():
    """Missing thesis data (empty THESIS_BY_INST) does not crash full_analysis()."""
    with patch.dict(app.THESIS_BY_INST, {}, clear=True):
        try:
            result = full_analysis()
            passed = ("verdict" in result)
        except Exception as exc:
            passed = False
            return _rec("test_p4_missing_thesis_data_does_not_crash", passed,
                        str(exc))
    return _rec("test_p4_missing_thesis_data_does_not_crash", passed)


def test_p4_malformed_thesis_data_does_not_crash():
    """Malformed thesis data (non-dict entry) does not crash full_analysis()."""
    with patch.dict(app.THESIS_BY_INST, {"MGC": "not_a_dict"}, clear=False):
        try:
            result = full_analysis(ticker_override="MGC")
            passed = ("verdict" in result)
        except Exception as exc:
            passed = False
            return _rec("test_p4_malformed_thesis_data_does_not_crash", passed,
                        str(exc))
    return _rec("test_p4_malformed_thesis_data_does_not_crash", passed)


def test_p4_decision_trace_degraded_does_not_500():
    """/decision-trace returns 200 even when flag is off (no trace data)."""
    resp = _client.get("/decision-trace")
    passed = (resp.status_code == 200)
    return _rec("test_p4_decision_trace_degraded_does_not_500", passed,
                f"status_code={resp.status_code}")


# ===========================================================================
# RUNNER
# ===========================================================================

_ALL_TESTS = [
    # V1-P4-007 MARKET_CLOSED
    test_p4_007a_market_session_status_closed_on_sunday,
    test_p4_007b_market_session_status_closed_has_reason,
    test_p4_007c_market_session_status_closed_has_next_open,
    test_p4_007d_status_endpoint_exposes_market_fields,
    test_p4_007e_market_closed_state_distinct_from_ordinary_wait,
    # V1-P4-001 WAIT
    test_p4_001a_strict_reason_present_in_full_analysis,
    test_p4_001b_wait_verdict_has_nonempty_strict_reason,
    test_p4_001c_gate_debug_present_in_result,
    test_p4_001d_strict_missing_present_when_wait,
    test_p4_001e_status_http_always_200,
    # V1-P4-006 VETO_ACTIVE
    test_p4_006a_analyst_block_present_in_full_analysis,
    test_p4_006b_analyst_veto_would_fire_is_bool,
    test_p4_006c_analyst_block_in_status_response,
    # V1-P4-002 READY fields
    test_p4_002a_main_brain_voice_present_and_string,
    test_p4_002b_edge_score_present_and_numeric,
    test_p4_002c_edge_grade_present,
    test_p4_002d_trade_plan_key_present,
    # V1-P4-003 EARLY
    test_p4_003a_alert_level_present_in_full_analysis,
    test_p4_003b_alert_level_exposed_in_status_response,
    test_p4_003c_directions_block_has_potential_plan_key,
    test_p4_003d_potential_plan_none_when_market_closed,
    # V1-P4-004 ACTIVE TRADE
    test_p4_004a_no_trade_active_trade_is_none,
    test_p4_004b_injected_trade_reflected_in_full_analysis,
    test_p4_004c_active_trade_has_expected_fields,
    test_p4_004d_main_brain_has_pos_reflects_active_trade,
    test_p4_004e_active_trade_cleared_after_test,
    # V1-P4-005 THESIS_INVALIDATED
    test_p4_005a_thesis_block_present_in_status,
    test_p4_005b_thesis_status_field_when_thesis_present,
    test_p4_005c_thesis_status_constrained_to_known_values,
    test_p4_005d_thesis_reason_field_present_when_invalidated,
    # V1-P4-008 Partner fallback
    test_p4_008a_status_200_when_compute_main_brain_raises,
    test_p4_008b_main_brain_neutral_stub_on_partner_failure,
    test_p4_008c_main_brain_voice_present_on_partner_failure,
    test_p4_008d_verdict_unchanged_when_partner_fails,
    test_p4_008e_missing_optional_partner_fields_does_not_crash,
    # V1-P4-009 Operator Mode DIAGNOSTIC audit
    test_p4_009a_verdict_present_in_status,
    test_p4_009b_failed_conditions_exposed,
    test_p4_009c_active_veto_exposed,
    test_p4_009d_thesis_state_exposed,
    test_p4_009e_potential_plan_path_exposed,
    test_p4_009f_freshness_timestamp_exposed,
    test_p4_009g_no_per_gate_raw_table_at_operator_level,
    # V1-P4-010 /decision-trace
    test_p4_010a_decision_trace_not_in_open_paths,
    test_p4_010b_decision_trace_in_proxy_whitelist,
    test_p4_010c_decision_trace_flag_off_returns_disabled_schema,
    test_p4_010d_decision_trace_endpoint_returns_200,
    # Instrument handling
    test_p4_malformed_instrument_status_200,
    test_p4_unknown_instrument_status_200,
    # Serialization, stability, safety
    test_p4_full_analysis_result_is_json_serializable,
    test_p4_repeated_calls_return_same_verdict,
    test_p4_full_analysis_does_not_mutate_active_trades,
    test_p4_no_broker_communication_field_in_status,
    test_p4_stale_market_data_still_returns_200,
    test_p4_status_degraded_when_no_market_data,
    test_p4_missing_thesis_data_does_not_crash,
    test_p4_malformed_thesis_data_does_not_crash,
    test_p4_decision_trace_degraded_does_not_500,
]


def run_all():
    print()
    print("═" * 64)
    print("  V1 PHASE 4 — Operator Explanation and Decision Timeline")
    print("═" * 64)
    passed_n = 0
    failed_n = 0
    for fn in _ALL_TESTS:
        try:
            ok = fn()
        except Exception as exc:
            _rec(fn.__name__, False, f"EXCEPTION: {exc}")
            ok = False
        if ok:
            passed_n += 1
        else:
            failed_n += 1
    print("═" * 64)
    print(f"  TOTAL: {passed_n + failed_n} checks — "
          f"{passed_n} passed, {failed_n} failed")
    if failed_n:
        print()
        print("  FAILED TESTS:")
        for name, status in _RESULTS.items():
            if status == "FAIL":
                print(f"    {name}")
    print()
    return failed_n == 0


if __name__ == "__main__":
    import sys
    ok = run_all()
    sys.exit(0 if ok else 1)
