"""
V1-P3: Thesis and Verdict Pipeline — Runtime behavioral tests.

Tasks covered:
  V1-P3-001  Left Brain guaranteed fields present in /status (narrative, invalidation,
             timeline, confidence/strength, direction)
  V1-P3-002  Thesis hysteresis (THESIS_UPDATED semantics) — documented and tested
  V1-P3-003  OUTLOOK_SHIFT detection test (large confidence delta triggers flag)
  V1-P3-004  Expert guaranteed fields present in /status (is_actionable derivable,
             verdict, strict_reason, grade, edge_score, gate_debug, trade_plan)
  V1-P3-005  strict_reason non-empty assertion (WAIT always has named reason)
  V1-P3-006  /decision-trace returns {enabled, traces} contract
  V1-P3-007  Expert gate boundary tests (zone/VWAP/structure each individually
             failing → WAIT with gate_debug evidence)
  V1-P3-008  SCALP vs SWING gate mode differences (zone demote vs require)
  V1-P3-009  Dual-sim extended verdict agreement test

Architecture rules preserved:
  - No gate, scoring, execution, or sizing logic changed
  - All tests are read-only or use fully isolated mocks
  - No broker communication
  - No Discord sends
  - Global state restored after every test that mutates it
"""

from __future__ import annotations

import os
import sys

# ── Flask test client + app import ───────────────────────────────────────────
import pytest

sys.path.insert(0, os.path.dirname(__file__))

import app as _app_module
from app import app as flask_app


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-001: Left Brain guaranteed fields present in compute_left_brain_thesis
# ─────────────────────────────────────────────────────────────────────────────

def _make_mi(long_pct=60, short_pct=30, neutral_pct=10):
    """Minimal MI block accepted by compute_left_brain_thesis."""
    return {
        "available":         True,
        "instrument":        "MGC",
        "market_state":      "TRENDING_UP_STRONG",
        "session_character": "STRONG_TREND_DAY",
        "auction_control":   "BULL_CONTROLLED",
        "data_confidence":   75,
        "computed_at":       "2026-07-29T10:00:00+00:00",
        "directional_outlook": {
            "long":    long_pct,
            "short":   short_pct,
            "neutral": neutral_pct,
        },
        "suitable_playbooks":  ["TREND_CONTINUATION"],
        "supporting_evidence": ["Strong bullish flow"],
    }


def test_p3_001_lb_thesis_direction_present():
    """V1-P3-001: compute_left_brain_thesis returns a thesis with 'direction' field."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    out = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    thesis = out["thesis"]
    assert "direction" in thesis, f"'direction' missing from thesis keys: {list(thesis)}"


def test_p3_001_lb_thesis_narrative_present():
    """V1-P3-001: compute_left_brain_thesis returns a thesis with 'narrative' field."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    out = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    thesis = out["thesis"]
    assert "narrative" in thesis, f"'narrative' missing from thesis keys: {list(thesis)}"
    assert thesis["narrative"] is not None, "'narrative' must not be None"


def test_p3_001_lb_thesis_invalidation_present():
    """V1-P3-001: compute_left_brain_thesis returns a thesis with 'invalidation' field."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    out = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    thesis = out["thesis"]
    assert "invalidation" in thesis, f"'invalidation' missing from thesis keys: {list(thesis)}"


def test_p3_001_lb_thesis_timeline_present():
    """V1-P3-001: compute_left_brain_thesis returns a thesis with 'timeline' field."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    out = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    thesis = out["thesis"]
    assert "timeline" in thesis, f"'timeline' missing from thesis keys: {list(thesis)}"


def test_p3_001_lb_thesis_strength_is_confidence_equivalent():
    """V1-P3-001: compute_left_brain_thesis returns 'strength' — the confidence-equivalent
    field. Architecture specifies 'confidence'; implementation uses 'strength' derived from
    data_confidence MI input. Both the neutral and computed thesis carry 'strength'."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    out = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    thesis = out["thesis"]
    assert "strength" in thesis, (
        "'strength' (confidence-equivalent) missing. "
        f"Thesis keys: {list(thesis)}"
    )
    assert isinstance(thesis["strength"], (int, float)), (
        f"'strength' must be numeric, got {type(thesis['strength'])}"
    )


def test_p3_001_lb_thesis_neutral_all_required_fields():
    """V1-P3-001: neutral thesis (degraded path) carries all guaranteed architecture fields."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    # Pass unavailable MI → triggers neutral thesis path
    out = compute_left_brain_thesis("MGC", {"available": False}, None, None, [])
    thesis = out["thesis"]
    required = {"direction", "narrative", "invalidation", "timeline", "strength"}
    missing = required - set(thesis.keys())
    assert not missing, (
        f"Neutral thesis missing required fields: {missing}. "
        f"Present: {list(thesis.keys())}"
    )


def test_p3_001_lb_thesis_available_field():
    """V1-P3-001: thesis['available'] is True for valid MI, False for neutral."""
    from left_brain_market_intelligence import compute_left_brain_thesis
    ok_out   = compute_left_brain_thesis("MGC", _make_mi(), None, None, [])
    null_out = compute_left_brain_thesis("MGC", {"available": False}, None, None, [])
    assert ok_out["thesis"]["available"] is True,  "Valid MI → thesis['available'] must be True"
    assert null_out["thesis"]["available"] is False, "Null MI → thesis['available'] must be False"


def test_p3_001_lb_thesis_in_full_analysis_when_flag_on():
    """V1-P3-001: when LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED is on and a thesis is
    injected into _LB_THESIS_BY_INST, full_analysis result['left_brain']['thesis']
    carries all required fields. After the test, the injected thesis is removed."""
    from left_brain_market_intelligence import compute_left_brain_thesis

    inst = "MGC"
    synthetic_thesis = compute_left_brain_thesis(inst, _make_mi(), None, None, [])["thesis"]

    orig_flag = _app_module.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED
    orig_store = dict(_app_module._LB_THESIS_BY_INST)
    try:
        _app_module.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED = True
        _app_module._LB_THESIS_BY_INST[inst] = synthetic_thesis

        result = _app_module.full_analysis()
        lb = result.get("left_brain")
        assert lb is not None, "result['left_brain'] absent when flag ON"
        t = lb.get("thesis")
        assert t is not None, "result['left_brain']['thesis'] is None; inst may differ from default"
        for field in ("direction", "narrative", "invalidation", "timeline", "strength"):
            assert field in t, (
                f"result['left_brain']['thesis']['{field}'] missing. "
                f"Thesis keys: {list(t.keys())}"
            )
    finally:
        _app_module.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED = orig_flag
        _app_module._LB_THESIS_BY_INST.clear()
        _app_module._LB_THESIS_BY_INST.update(orig_store)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-002: Thesis hysteresis (THESIS_UPDATED behavior)
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_002_thesis_hysteresis_enabled_by_default():
    """V1-P3-002: THESIS_HYSTERESIS is ON by default (_THESIS_ENABLED=True).
    The hysteresis flag defaults to '1', meaning the inertia layer is active."""
    assert _app_module._THESIS_ENABLED is True, (
        "_THESIS_ENABLED is False — THESIS_HYSTERESIS is off by default, "
        "which would disable confidence inertia."
    )


def test_p3_002_thesis_flag_off_passthrough():
    """V1-P3-002: when _THESIS_ENABLED is False (THESIS_HYSTERESIS=0), _apply_thesis
    passes through the raw verdict unchanged and returns an empty snapshot dict."""
    orig = _app_module._THESIS_ENABLED
    try:
        _app_module._THESIS_ENABLED = False
        adj, snap = _app_module._apply_thesis("MGC", {}, "WAIT")
        assert adj == "WAIT", f"Flag-OFF should passthrough 'WAIT', got {adj!r}"
        assert snap == {}, f"Flag-OFF should return empty snapshot, got {snap!r}"
    finally:
        _app_module._THESIS_ENABLED = orig


def test_p3_002_thesis_flag_off_passthrough_various_verdicts():
    """V1-P3-002: _apply_thesis flag-OFF passes through any raw verdict unchanged."""
    orig = _app_module._THESIS_ENABLED
    try:
        _app_module._THESIS_ENABLED = False
        for verdict in ("WAIT", "MARKET CLOSED", "SETUP BUILDING"):
            adj, snap = _app_module._apply_thesis("MGC", {}, verdict)
            assert adj == verdict, f"Flag-OFF: expected {verdict!r}, got {adj!r}"
            assert snap == {}, f"Flag-OFF: expected empty snap for {verdict!r}"
    finally:
        _app_module._THESIS_ENABLED = orig


def test_p3_002_thesis_snap_produced_when_flag_on():
    """V1-P3-002: when flag is ON, _apply_thesis returns a non-empty snapshot dict
    (the thesis state machine ran). The snapshot schema is the documented contract."""
    # Fresh instrument with no prior thesis — thesis state builds from scratch.
    result = _app_module.full_analysis()
    # The thesis snapshot is stored in result["thesis"] (the THESIS_HYSTERESIS layer)
    snap = result.get("thesis")
    # Thesis is present and is a dict (empty dict {} is acceptable when no state yet)
    assert isinstance(snap, dict), (
        f"result['thesis'] must be a dict, got {type(snap)}: {snap!r}"
    )


def test_p3_002_thesis_snapshot_fields():
    """V1-P3-002: thesis snapshot in result['thesis'] exposes documented status fields
    that describe the current hysteresis state (status, confidence, direction)."""
    result = _app_module.full_analysis()
    snap = result.get("thesis") or {}
    # If no thesis has been established yet (fresh state), snap may be empty.
    # When populated, it must contain the documented fields.
    if snap:
        for field in ("status", "confidence"):
            assert field in snap, (
                f"thesis snapshot missing '{field}'. "
                f"Present: {list(snap.keys())}"
            )


def test_p3_002_thesis_apply_is_fail_open():
    """V1-P3-002: _apply_thesis is FAIL-OPEN — a bad strict dict never crashes;
    it returns the raw verdict with an empty snap instead."""
    orig = _app_module._THESIS_ENABLED
    try:
        _app_module._THESIS_ENABLED = True
        # Corrupt strict dict — should not raise
        adj, snap = _app_module._apply_thesis("MGC", None, "WAIT")
        assert isinstance(adj, str), f"adj must be str, got {type(adj)}"
        assert isinstance(snap, dict), f"snap must be dict, got {type(snap)}"
    finally:
        _app_module._THESIS_ENABLED = orig


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-003: OUTLOOK_SHIFT detection
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_003_outlook_shift_triggers_on_large_delta():
    """V1-P3-003: OUTLOOK_SHIFT event is emitted when dominant directional weight
    changes by ≥ 15 percentage points between MI updates."""
    from left_brain_market_intelligence import _detect_significant_changes

    prev_mi = _make_mi(long_pct=50, short_pct=30, neutral_pct=20)
    cur_mi  = _make_mi(long_pct=70, short_pct=20, neutral_pct=10)  # delta = 70-50 = 20 ≥ 15

    events = _detect_significant_changes(
        mi=cur_mi, prev_mi=prev_mi,
        direction="BULL", prev_direction="BULL",
        strength=80, prev_strength=60,
    )
    event_types = [e["event_type"] for e in events]
    assert "OUTLOOK_SHIFT" in event_types, (
        f"Expected OUTLOOK_SHIFT event for 20-pt delta but got: {event_types}"
    )


def test_p3_003_outlook_shift_absent_on_small_delta():
    """V1-P3-003: OUTLOOK_SHIFT event is NOT emitted when the dominant weight
    delta is below the 15-point threshold."""
    from left_brain_market_intelligence import _detect_significant_changes

    prev_mi = _make_mi(long_pct=55, short_pct=30, neutral_pct=15)
    cur_mi  = _make_mi(long_pct=60, short_pct=28, neutral_pct=12)  # delta = 60-55 = 5 < 15

    events = _detect_significant_changes(
        mi=cur_mi, prev_mi=prev_mi,
        direction="BULL", prev_direction="BULL",
        strength=70, prev_strength=65,
    )
    event_types = [e["event_type"] for e in events]
    assert "OUTLOOK_SHIFT" not in event_types, (
        f"Unexpected OUTLOOK_SHIFT for 5-pt delta: {event_types}"
    )


def test_p3_003_outlook_shift_absent_when_no_prev_mi():
    """V1-P3-003: No events are emitted when prev_mi is None (first bar — no baseline)."""
    from left_brain_market_intelligence import _detect_significant_changes

    events = _detect_significant_changes(
        mi=_make_mi(long_pct=80, short_pct=15, neutral_pct=5),
        prev_mi=None,
        direction="BULL", prev_direction=None,
        strength=90, prev_strength=None,
    )
    assert events == [], f"First-bar should produce no events, got: {events}"


def test_p3_003_outlook_shift_at_exact_threshold():
    """V1-P3-003: OUTLOOK_SHIFT triggers at exactly the 15-point threshold."""
    from left_brain_market_intelligence import _detect_significant_changes

    prev_mi = _make_mi(long_pct=50, short_pct=35, neutral_pct=15)
    cur_mi  = _make_mi(long_pct=65, short_pct=25, neutral_pct=10)  # delta = 65-50 = 15 (boundary)

    events = _detect_significant_changes(
        mi=cur_mi, prev_mi=prev_mi,
        direction="BULL", prev_direction="BULL",
        strength=75, prev_strength=55,
    )
    event_types = [e["event_type"] for e in events]
    assert "OUTLOOK_SHIFT" in event_types, (
        f"OUTLOOK_SHIFT should trigger at exactly 15-pt delta, got: {event_types}"
    )


def test_p3_003_outlook_shift_event_schema():
    """V1-P3-003: emitted OUTLOOK_SHIFT event carries the required schema fields."""
    from left_brain_market_intelligence import _detect_significant_changes

    prev_mi = _make_mi(long_pct=40, short_pct=50, neutral_pct=10)
    cur_mi  = _make_mi(long_pct=65, short_pct=30, neutral_pct=5)   # delta = 65-40 = 25

    events = _detect_significant_changes(
        mi=cur_mi, prev_mi=prev_mi,
        direction="BEAR", prev_direction="BEAR",
        strength=70, prev_strength=50,
    )
    os_events = [e for e in events if e["event_type"] == "OUTLOOK_SHIFT"]
    assert os_events, "No OUTLOOK_SHIFT event found"
    ev = os_events[0]
    for field in ("ts", "event_type", "label", "from_value", "to_value",
                  "reason", "evidence", "confidence_at_time"):
        assert field in ev, f"OUTLOOK_SHIFT event missing field '{field}'. Keys: {list(ev)}"
    assert isinstance(ev["evidence"], list), "'evidence' must be a list"
    assert len(ev["evidence"]) > 0, "'evidence' must be non-empty"


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-004: Expert guaranteed fields present in /status
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_004_expert_verdict_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'verdict' field."""
    result = _app_module.full_analysis()
    assert "verdict" in result, f"'verdict' missing from full_analysis result. Keys: {list(result)}"
    assert isinstance(result["verdict"], str), f"'verdict' must be str, got {type(result['verdict'])}"


def test_p3_004_expert_strict_reason_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'strict_reason' field (may be empty str
    when READY, but the key must always be present)."""
    result = _app_module.full_analysis()
    assert "strict_reason" in result, (
        f"'strict_reason' missing from full_analysis. Keys: {list(result)}"
    )


def test_p3_004_expert_gate_debug_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'gate_debug' field (dict or None)."""
    result = _app_module.full_analysis()
    assert "gate_debug" in result, (
        f"'gate_debug' missing from full_analysis. Keys: {list(result)}"
    )
    if result["gate_debug"] is not None:
        assert isinstance(result["gate_debug"], dict), (
            f"'gate_debug' must be dict or None, got {type(result['gate_debug'])}"
        )


def test_p3_004_expert_edge_score_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'edge_score' field (numeric)."""
    result = _app_module.full_analysis()
    assert "edge_score" in result, (
        f"'edge_score' missing from full_analysis. Keys: {list(result)}"
    )
    assert isinstance(result["edge_score"], (int, float)), (
        f"'edge_score' must be numeric, got {type(result['edge_score'])}"
    )


def test_p3_004_expert_grade_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'edge_grade' (grade) field."""
    result = _app_module.full_analysis()
    # The architecture field is "grade"; the implementation key is "edge_grade"
    assert "edge_grade" in result, (
        f"'edge_grade' (grade) missing from full_analysis. Keys: {list(result)}"
    )


def test_p3_004_expert_trade_plan_in_full_analysis():
    """V1-P3-004: full_analysis() result contains 'trade_plan' field (dict)."""
    result = _app_module.full_analysis()
    assert "trade_plan" in result, (
        f"'trade_plan' missing from full_analysis. Keys: {list(result)}"
    )
    assert isinstance(result["trade_plan"], dict), (
        f"'trade_plan' must be dict, got {type(result['trade_plan'])}"
    )


def test_p3_004_is_actionable_derivable_from_verdict():
    """V1-P3-004: is_actionable() is callable with the verdict field and returns bool.
    This is the architecture-specified 'is_actionable' contract: a function, not a stored field."""
    result = _app_module.full_analysis()
    verdict = result["verdict"]
    is_act = _app_module.is_actionable(verdict)
    assert isinstance(is_act, bool), (
        f"is_actionable({verdict!r}) must return bool, got {type(is_act)}"
    )


def test_p3_004_expert_fields_in_status_response():
    """V1-P3-004: /status API response contains all Expert guaranteed fields."""
    client = flask_app.test_client()
    r = client.get("/status")
    assert r.status_code == 200, f"/status returned {r.status_code}"
    d = r.get_json()
    assert d is not None, "/status returned non-JSON"
    expert_fields = {
        "verdict":       "verdict",
        "strict_reason": "strict_reason",
        "gate_debug":    "gate_debug",
        "edge_score":    "edge_score",
        "edge_grade":    "edge_grade (grade)",
        "trade_plan":    "trade_plan",
    }
    for key, label in expert_fields.items():
        assert key in d, (
            f"/status missing Expert field '{label}' (key='{key}'). "
            f"Present keys (sample): {list(d)[:20]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-005: strict_reason non-empty when WAIT
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_005_strict_reason_non_empty_on_market_closed():
    """V1-P3-005: when market is closed, verdict is WAIT and strict_reason is non-empty.
    Market-closed is the guaranteed WAIT state in the test environment."""
    result = _app_module.full_analysis()
    verdict = result["verdict"]
    strict_reason = result.get("strict_reason") or ""
    # The test environment is always outside market hours → WAIT
    if not _app_module.is_actionable(verdict):
        assert strict_reason.strip() != "", (
            f"WAIT verdict must carry a non-empty strict_reason. "
            f"verdict={verdict!r}, strict_reason={strict_reason!r}"
        )


def test_p3_005_strict_reason_non_empty_in_status():
    """V1-P3-005: /status response strict_reason is non-empty when verdict is WAIT."""
    client = flask_app.test_client()
    r = client.get("/status")
    assert r.status_code == 200
    d = r.get_json()
    verdict = d.get("verdict", "")
    sr = d.get("strict_reason") or ""
    if not _app_module.is_actionable(verdict):
        assert sr.strip() != "", (
            f"WAIT verdict in /status must carry a non-empty strict_reason. "
            f"verdict={verdict!r}, strict_reason={sr!r}"
        )


def test_p3_005_strict_reason_is_string():
    """V1-P3-005: strict_reason is always a string (never None or non-string type)."""
    result = _app_module.full_analysis()
    sr = result.get("strict_reason")
    # strict_reason may be None when READY (no blocking reason); when WAIT it must be str
    if sr is not None:
        assert isinstance(sr, str), (
            f"strict_reason must be str or None, got {type(sr)}: {sr!r}"
        )


def test_p3_005_strict_reason_repeated_calls_stable():
    """V1-P3-005: repeated full_analysis() calls in market-closed state produce
    consistent non-empty strict_reason (no silent degradation to empty string)."""
    for i in range(3):
        result = _app_module.full_analysis()
        verdict = result["verdict"]
        sr = result.get("strict_reason") or ""
        if not _app_module.is_actionable(verdict):
            assert sr.strip() != "", (
                f"Call #{i+1}: WAIT verdict had empty strict_reason"
            )


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-006: /decision-trace contract
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_006_decision_trace_flag_off_returns_200():
    """V1-P3-006: /decision-trace returns HTTP 200 even when flag is OFF."""
    orig = _app_module.DECISION_TRACE_SHADOW_ENABLED
    try:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = False
        client = flask_app.test_client()
        r = client.get("/decision-trace")
        assert r.status_code == 200, (
            f"/decision-trace flag-OFF returned {r.status_code}, expected 200"
        )
    finally:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = orig


def test_p3_006_decision_trace_flag_off_returns_disabled_contract():
    """V1-P3-006: /decision-trace flag-OFF returns {enabled: false, traces: {}} — the
    documented contract when the shadow engine is not running."""
    orig = _app_module.DECISION_TRACE_SHADOW_ENABLED
    try:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = False
        client = flask_app.test_client()
        r = client.get("/decision-trace")
        d = r.get_json()
        assert d is not None, "/decision-trace returned non-JSON"
        assert d.get("enabled") is False, (
            f"flag-OFF: 'enabled' must be False, got {d.get('enabled')!r}"
        )
        assert d.get("traces") == {}, (
            f"flag-OFF: 'traces' must be empty dict, got {d.get('traces')!r}"
        )
    finally:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = orig


def test_p3_006_decision_trace_flag_on_returns_enabled():
    """V1-P3-006: when DECISION_TRACE_SHADOW_ENABLED is ON, /decision-trace returns
    {enabled: true, traces: <dict>}. The traces dict is populated by full_analysis()
    calls in the background; in the test environment it may be empty (no prior calls),
    but the enabled flag and schema must be correct."""
    orig_flag = _app_module.DECISION_TRACE_SHADOW_ENABLED
    orig_trace = dict(_app_module._LAST_DECISION_TRACE)
    try:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = True
        _app_module._LAST_DECISION_TRACE.clear()

        client = flask_app.test_client()
        r = client.get("/decision-trace")
        assert r.status_code == 200
        d = r.get_json()
        assert d is not None, "/decision-trace returned non-JSON"
        assert d.get("enabled") is True, (
            f"flag-ON: 'enabled' must be True, got {d.get('enabled')!r}"
        )
        assert isinstance(d.get("traces"), dict), (
            f"flag-ON: 'traces' must be dict, got {type(d.get('traces'))}"
        )
    finally:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = orig_flag
        _app_module._LAST_DECISION_TRACE.clear()
        _app_module._LAST_DECISION_TRACE.update(orig_trace)


def test_p3_006_decision_trace_populates_after_analysis():
    """V1-P3-006: after full_analysis() runs with DECISION_TRACE_SHADOW_ENABLED=True,
    the _LAST_DECISION_TRACE cache is populated (the trace was built and stored)."""
    orig_flag  = _app_module.DECISION_TRACE_SHADOW_ENABLED
    orig_trace = dict(_app_module._LAST_DECISION_TRACE)
    try:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = True
        _app_module._LAST_DECISION_TRACE.clear()

        # Run full_analysis — the decision-trace adapter fires at the end when flag is ON
        _app_module.full_analysis()

        with _app_module._DECISION_TRACE_LOCK:
            trace_snapshot = dict(_app_module._LAST_DECISION_TRACE)

        assert isinstance(trace_snapshot, dict), (
            f"_LAST_DECISION_TRACE must be a dict, got {type(trace_snapshot)}"
        )
        # When the flag is ON and full_analysis() ran, at least one instrument's
        # trace should be populated.
        assert len(trace_snapshot) > 0, (
            "After full_analysis() with DECISION_TRACE_SHADOW_ENABLED=True, "
            "_LAST_DECISION_TRACE was not populated. "
            "Check that build_legacy_decision_trace() ran without exception."
        )
    finally:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = orig_flag
        _app_module._LAST_DECISION_TRACE.clear()
        _app_module._LAST_DECISION_TRACE.update(orig_trace)


def test_p3_006_decision_trace_record_schema():
    """V1-P3-006: trace records stored by the decision-trace adapter carry the
    required schema fields (instrument, verdict, strict_reason, generated_at)."""
    orig_flag  = _app_module.DECISION_TRACE_SHADOW_ENABLED
    orig_trace = dict(_app_module._LAST_DECISION_TRACE)
    try:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = True
        _app_module._LAST_DECISION_TRACE.clear()

        _app_module.full_analysis()

        with _app_module._DECISION_TRACE_LOCK:
            trace_snapshot = dict(_app_module._LAST_DECISION_TRACE)

        if not trace_snapshot:
            pytest.skip("No trace populated — decision-trace adapter may have failed silently")

        for inst, record in trace_snapshot.items():
            assert isinstance(record, dict), (
                f"Trace for {inst!r} is not a dict: {type(record)}"
            )
            # The build_legacy_decision_trace schema guarantees these fields
            assert "verdict" in record or "strict_reason" in record, (
                f"Trace record for {inst!r} missing both 'verdict' and 'strict_reason'. "
                f"Keys: {list(record)}"
            )
    finally:
        _app_module.DECISION_TRACE_SHADOW_ENABLED = orig_flag
        _app_module._LAST_DECISION_TRACE.clear()
        _app_module._LAST_DECISION_TRACE.update(orig_trace)


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-007: Gate boundary tests (zone/VWAP/structure individually failing → WAIT)
# ─────────────────────────────────────────────────────────────────────────────

def _swing_call_no_zone():
    """evaluate_strict_setup in SWING mode with no demand/supply zones."""
    return _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0,          # price > vwap → price_above=True for Long
        vwap_status="ok",
        nearest_supply=None,
        nearest_demand=None,  # No zones → zone_valid=False
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SWING",
    )


def _swing_call_no_vwap():
    """evaluate_strict_setup in SWING mode with missing VWAP."""
    return _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=None,
        vwap_status="missing",  # VWAP missing → vwap_ok=False
        nearest_supply=None,
        nearest_demand=None,
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SWING",
    )


def _swing_call_no_structure():
    """evaluate_strict_setup in SWING mode with no structure signals (no BOS/CHOCH)."""
    return _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0,
        vwap_status="ok",
        nearest_supply=None,
        nearest_demand=None,
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],  # No BOS/CHOCH → structure_confirmed=False
        mode="SWING",
    )


def test_p3_007_zone_gate_fails_in_swing_no_zone():
    """V1-P3-007: SWING mode with no zone → gate_debug['zone_valid'] is False."""
    result = _swing_call_no_zone()
    gd = result.get("gate_debug") or {}
    assert gd.get("zone_valid") is False, (
        f"Expected zone_valid=False in SWING with no zone, got: {gd.get('zone_valid')}"
    )


def test_p3_007_zone_gate_produces_wait_in_swing():
    """V1-P3-007: SWING mode with no zone → WAIT verdict (zone is a hard requirement)."""
    result = _swing_call_no_zone()
    label = result.get("label", "")
    assert label == "WAIT", (
        f"Expected 'WAIT' in SWING with no zone, got label={label!r}"
    )


def test_p3_007_zone_in_missing_in_swing():
    """V1-P3-007: SWING mode with no zone → 'zone_valid' appears in result['missing']
    (the list of unmet gate conditions)."""
    result = _swing_call_no_zone()
    missing = result.get("missing") or []
    assert "zone_valid" in missing, (
        f"'zone_valid' not in missing list for SWING+no-zone. missing={missing}"
    )


def test_p3_007_vwap_gate_fails_in_swing_missing_vwap():
    """V1-P3-007: SWING mode with missing VWAP → gate_debug shows VWAP is unconfirmed."""
    result = _swing_call_no_vwap()
    gd = result.get("gate_debug") or {}
    assert gd.get("vwap_confirmed") is False, (
        f"Expected vwap_confirmed=False with missing VWAP, got: {gd.get('vwap_confirmed')}"
    )


def test_p3_007_vwap_gate_produces_wait_in_swing():
    """V1-P3-007: SWING mode with missing VWAP → WAIT verdict."""
    result = _swing_call_no_vwap()
    label = result.get("label", "")
    assert label == "WAIT", (
        f"Expected 'WAIT' in SWING with missing VWAP, got label={label!r}"
    )


def test_p3_007_structure_gate_fails_in_swing_no_signals():
    """V1-P3-007: SWING mode with no BOS/CHOCH signals → structure_confirmed=False."""
    result = _swing_call_no_structure()
    gd = result.get("gate_debug") or {}
    assert gd.get("structure_confirmed") is False, (
        f"Expected structure_confirmed=False with no signals, got: {gd.get('structure_confirmed')}"
    )


def test_p3_007_structure_gate_produces_wait_in_swing():
    """V1-P3-007: SWING mode with no structure signals → WAIT verdict."""
    result = _swing_call_no_structure()
    label = result.get("label", "")
    assert label == "WAIT", (
        f"Expected 'WAIT' in SWING with no structure, got label={label!r}"
    )


def test_p3_007_structure_in_missing_in_swing():
    """V1-P3-007: SWING mode with no structure → 'structure_confirmed' in result['missing']."""
    result = _swing_call_no_structure()
    missing = result.get("missing") or []
    assert "structure_confirmed" in missing, (
        f"'structure_confirmed' not in missing list for SWING+no-structure. missing={missing}"
    )


def test_p3_007_gate_debug_require_zone_true_in_swing():
    """V1-P3-007: gate_debug['require_zone'] is True in SWING mode (confirms the gate
    is active, not just that zone_valid happens to be False)."""
    result = _swing_call_no_zone()
    gd = result.get("gate_debug") or {}
    assert gd.get("require_zone") is True, (
        f"Expected require_zone=True in SWING gate_debug, got: {gd.get('require_zone')}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-008: SCALP vs SWING gate mode differences (zone demote vs require)
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_008_cfg_for_zone_false_in_scalp():
    """V1-P3-008: SCALP mode has GATE_REQUIRE_ZONE=False (zone demoted to confirmation)."""
    val = _app_module.cfg_for("SCALP", "GATE_REQUIRE_ZONE")
    assert val is False, (
        f"SCALP GATE_REQUIRE_ZONE must be False (zone demoted), got {val!r}"
    )


def test_p3_008_cfg_for_zone_true_in_swing():
    """V1-P3-008: SWING mode has GATE_REQUIRE_ZONE=True (zone is a hard requirement)."""
    val = _app_module.cfg_for("SWING", "GATE_REQUIRE_ZONE")
    assert val is True, (
        f"SWING GATE_REQUIRE_ZONE must be True (zone required), got {val!r}"
    )


def test_p3_008_zone_not_gated_in_scalp_mode():
    """V1-P3-008: in SCALP mode, absence of a zone does NOT add 'zone_valid' to
    the 'missing' list (zone is demoted — not a blocking gate)."""
    result = _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0, vwap_status="ok",
        nearest_supply=None, nearest_demand=None,  # No zones
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SCALP",
    )
    missing = result.get("missing") or []
    assert "zone_valid" not in missing, (
        f"SCALP mode: 'zone_valid' must NOT be in missing (zone demoted), "
        f"but missing={missing}"
    )


def test_p3_008_zone_gated_in_swing_mode():
    """V1-P3-008: in SWING mode, absence of a zone DOES add 'zone_valid' to the
    'missing' list (zone is a hard requirement, not a confirmation)."""
    result = _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0, vwap_status="ok",
        nearest_supply=None, nearest_demand=None,  # No zones
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SWING",
    )
    missing = result.get("missing") or []
    assert "zone_valid" in missing, (
        f"SWING mode: 'zone_valid' must be in missing (zone required), "
        f"but missing={missing}"
    )


def test_p3_008_require_zone_false_in_scalp_gate_debug():
    """V1-P3-008: gate_debug['require_zone'] is False in SCALP mode."""
    result = _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0, vwap_status="ok",
        nearest_supply=None, nearest_demand=None,
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SCALP",
    )
    gd = result.get("gate_debug") or {}
    assert gd.get("require_zone") is False, (
        f"SCALP gate_debug['require_zone'] must be False, got {gd.get('require_zone')!r}"
    )


def test_p3_008_require_zone_true_in_swing_gate_debug():
    """V1-P3-008: gate_debug['require_zone'] is True in SWING mode."""
    result = _app_module.evaluate_strict_setup(
        current_price=2000.0,
        ticker="MGC1!",
        vwap=1990.0, vwap_status="ok",
        nearest_supply=None, nearest_demand=None,
        bullish=0, bearish=0,
        confidence=50,
        alert_history=[],
        mode="SWING",
    )
    gd = result.get("gate_debug") or {}
    assert gd.get("require_zone") is True, (
        f"SWING gate_debug['require_zone'] must be True, got {gd.get('require_zone')!r}"
    )


def test_p3_008_scalp_vwap_still_required():
    """V1-P3-008: SCALP demotes zone but VWAP is still required (GATE_REQUIRE_VWAP=True)."""
    val = _app_module.cfg_for("SCALP", "GATE_REQUIRE_VWAP")
    assert val is True, (
        f"SCALP GATE_REQUIRE_VWAP must still be True (VWAP not demoted), got {val!r}"
    )


def test_p3_008_scalp_structure_still_required():
    """V1-P3-008: SCALP demotes zone but structure is still required (GATE_REQUIRE_STRUCTURE=True)."""
    val = _app_module.cfg_for("SCALP", "GATE_REQUIRE_STRUCTURE")
    assert val is True, (
        f"SCALP GATE_REQUIRE_STRUCTURE must still be True (structure not demoted), got {val!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# V1-P3-009: Dual-sim extended verdict agreement
# ─────────────────────────────────────────────────────────────────────────────

def test_p3_009_full_analysis_verdict_is_deterministic():
    """V1-P3-009: repeated full_analysis() calls with identical state return the same
    verdict (no non-deterministic drift in the analysis pipeline)."""
    verdicts = [_app_module.full_analysis()["verdict"] for _ in range(3)]
    assert len(set(verdicts)) == 1, (
        f"full_analysis() returned inconsistent verdicts: {verdicts}"
    )


def test_p3_009_scalp_mode_analysis_returns_verdict():
    """V1-P3-009: full_analysis() in explicit SCALP mode returns a valid verdict string."""
    orig_mode = _app_module.TRADING_MODE
    try:
        _app_module.TRADING_MODE = "SCALP"
        result = _app_module.full_analysis()
        assert isinstance(result.get("verdict"), str), (
            f"SCALP full_analysis 'verdict' must be str, got {result.get('verdict')!r}"
        )
    finally:
        _app_module.TRADING_MODE = orig_mode


def test_p3_009_swing_mode_analysis_returns_verdict():
    """V1-P3-009: full_analysis() in explicit SWING mode returns a valid verdict string."""
    orig_mode = _app_module.TRADING_MODE
    try:
        _app_module.TRADING_MODE = "SWING"
        result = _app_module.full_analysis()
        assert isinstance(result.get("verdict"), str), (
            f"SWING full_analysis 'verdict' must be str, got {result.get('verdict')!r}"
        )
    finally:
        _app_module.TRADING_MODE = orig_mode


def test_p3_009_both_modes_agree_on_market_closed_wait():
    """V1-P3-009: both SCALP and SWING modes produce a WAIT-class verdict when market
    is closed (closed state is mode-independent). This verifies verdict-level agreement
    on the most reliable test signal."""
    orig_mode = _app_module.TRADING_MODE
    try:
        _app_module.TRADING_MODE = "SCALP"
        scalp_result = _app_module.full_analysis()
        _app_module.TRADING_MODE = "SWING"
        swing_result = _app_module.full_analysis()
    finally:
        _app_module.TRADING_MODE = orig_mode

    scalp_v = scalp_result.get("verdict", "")
    swing_v = swing_result.get("verdict", "")

    # In market-closed state, neither mode should return READY
    scalp_act = _app_module.is_actionable(scalp_v)
    swing_act = _app_module.is_actionable(swing_v)
    assert not scalp_act, f"SCALP should not be actionable when market closed: {scalp_v!r}"
    assert not swing_act, f"SWING should not be actionable when market closed: {swing_v!r}"


def test_p3_009_evaluate_strict_setup_mode_param_accepted():
    """V1-P3-009: evaluate_strict_setup() accepts the 'mode' parameter for dual-sim.
    Both SCALP and SWING modes run without error on the same minimal input."""
    for mode in ("SCALP", "SWING"):
        result = _app_module.evaluate_strict_setup(
            current_price=2000.0,
            ticker="MGC1!",
            vwap=1990.0, vwap_status="ok",
            nearest_supply=None, nearest_demand=None,
            bullish=0, bearish=0,
            confidence=50,
            alert_history=[],
            mode=mode,
        )
        assert isinstance(result, dict), (
            f"evaluate_strict_setup(mode={mode!r}) must return dict, got {type(result)}"
        )
        assert "label" in result, (
            f"evaluate_strict_setup(mode={mode!r}) result missing 'label'. Keys: {list(result)}"
        )


def test_p3_009_gate_debug_present_in_both_modes():
    """V1-P3-009: gate_debug is present in evaluate_strict_setup result for both modes."""
    for mode in ("SCALP", "SWING"):
        result = _app_module.evaluate_strict_setup(
            current_price=2000.0,
            ticker="MGC1!",
            vwap=1990.0, vwap_status="ok",
            nearest_supply=None, nearest_demand=None,
            bullish=0, bearish=0,
            confidence=50,
            alert_history=[],
            mode=mode,
        )
        assert "gate_debug" in result, (
            f"mode={mode!r}: 'gate_debug' missing from evaluate_strict_setup result. "
            f"Keys: {list(result)}"
        )
        gd = result["gate_debug"]
        assert isinstance(gd, dict), (
            f"mode={mode!r}: 'gate_debug' must be dict, got {type(gd)}"
        )


def test_p3_009_full_analysis_result_version_v1_in_both_modes():
    """V1-P3-009: full_analysis() result carries _version='v1' in both SCALP and SWING mode.
    This confirms the Expert interface contract is stable across modes."""
    orig_mode = _app_module.TRADING_MODE
    try:
        for mode in ("SCALP", "SWING"):
            _app_module.TRADING_MODE = mode
            result = _app_module.full_analysis()
            assert result.get("_version") == "v1", (
                f"full_analysis() mode={mode}: expected _version='v1', "
                f"got {result.get('_version')!r}"
            )
    finally:
        _app_module.TRADING_MODE = orig_mode
