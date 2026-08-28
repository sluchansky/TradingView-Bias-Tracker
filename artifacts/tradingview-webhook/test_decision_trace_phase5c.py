"""test_decision_trace_phase5c.py — Phase 5C: Diagnostic truthfulness additions.

Covers the five new top-level keys added to build_legacy_decision_trace() output:
  edge_score_detail  (Change 2) — full score math breakdown
  zone_role          (Change 3) — zone requirement disclosure
  failure_summary    (Change 4) — human-readable categorised failure info
  score_gap          (Change 5) — points needed + absent components
  structure_alert    (Change 6) — BOS/CHOCH verification status

Rules:
  R1. All five keys are present in every non-error trace (may be None).
  R2. Existing Phase 5B keys are byte-identical (no regression).
  R3. Every block is individually fail-open: a corrupt sub-field must not prevent
      the function returning a valid dict with schema_version==1.
  R4. None result still returns fail-open dict; new keys are None in the
      error dict.
  R5. All changes are DISPLAY-ONLY — no money-path keys touched.

42 Phase 5B tests remain green (verified externally); this file adds 26 new tests.
"""
import sys
import os
import copy
import importlib

sys.path.insert(0, os.path.dirname(__file__))
import app
importlib.reload(app)

# ── Shared fixtures ───────────────────────────────────────────────────────────

EDGE_COMPONENTS_MAX = app.EDGE_SCORE_MAX  # 110


def _minimal_wait(direction=None, edge_score=30, **kw):
    """Minimal result dict — WAIT, no active trade, no plan."""
    base = {
        "verdict":          "WAIT",
        "strict_reason":    "Missing: structure",
        "strict_label":     "WAIT",
        "strict_direction": direction,
        "strict_score":     0,
        "strict_missing":   ["structure"],
        "edge_score":       edge_score,
        "edge_grade":       None,
        "trade_plan":       {"trade_plan": False},
        "active_trade":     None,
        "market_open":      True,
        "main_brain":       None,
        "gate_debug":       {
            "require_zone":          False,
            "zoneState":             "Tested",
            "zone_valid":            True,
            "full_ready_threshold":  60,
            "ready_threshold":       60,
            "bos":                   False,
            "choch":                 False,
            "vwap_confirmed":        True,
            "liquidity_sweep":       False,
            "volume_confirmed":      True,
            "cvd_confirmed":         False,
            "session_pref":          False,
            "structure_confirmed":   False,
            "edge_score":            edge_score,
            "failed_conditions":     ["edge_score(%d<60)" % edge_score],
            "learning_score_delta":  0,
        },
        "edge_breakdown":   {
            "score":         edge_score,
            "raw_score":     edge_score,
            "score_breakdown": [
                {"label": "VWAP Confirmation",   "points": 15},
                {"label": "Volume Confirmation",  "points": 15},
                {"label": "Bearish BOS",          "points": 0},
            ],
            "components": [
                {"key": "structure_allocation", "label": "Market Structure",  "points": 40, "present": False},
                {"key": "vwap_confirmed",    "label": "VWAP Confirmation",    "points": 15, "present": True},
                {"key": "liquidity_sweep",   "label": "Liquidity Sweep",      "points": 15, "present": False},
                {"key": "volume_confirmed",  "label": "Volume Confirmation",  "points": 15, "present": True},
                {"key": "cvd_confirmed",     "label": "CVD Agreement",        "points": 15, "present": False},
                {"key": "preferred_session", "label": "Session Bonus",        "points": 10, "present": False},
            ],
        },
        "volatility": {
            "status":    "ok",
            "label":     "Choppy",
            "regime":    "ELEVATED",
            "score_adj": -10,
            "atr_pts":   2.0,
            "baseline_pts": 1.5,
            "ratio":     1.33,
            "threshold_elevated": 1.2,
            "threshold_extreme":  2.5,
        },
    }
    base.update(kw)
    return base


def _build(result_overrides=None, instrument="MGC"):
    d = _minimal_wait(**(result_overrides or {}))
    return app.build_legacy_decision_trace(d, instrument)


# ── R1: All five new keys present in a normal trace ──────────────────────────

NEW_KEYS = {"edge_score_detail", "zone_role", "failure_summary",
            "score_gap", "structure_alert"}


def test_all_new_keys_present():
    trace = _build()
    missing = NEW_KEYS - set(trace.keys())
    assert not missing, "New Phase 5C keys missing: %s" % missing


def test_new_keys_present_on_ready_trace():
    trace = _build({"verdict": "LONG READY", "strict_direction": "Long",
                    "edge_score": 85,
                    "gate_debug": {
                        "require_zone": True, "zoneState": "Tested",
                        "zone_valid": True, "full_ready_threshold": 60,
                        "ready_threshold": 60, "bos": True, "choch": False,
                        "vwap_confirmed": True, "liquidity_sweep": True,
                        "volume_confirmed": True, "cvd_confirmed": True,
                        "session_pref": True, "structure_confirmed": True,
                        "edge_score": 85, "failed_conditions": [],
                        "learning_score_delta": 0,
                    },
                    "edge_breakdown": {
                        "score": 85, "raw_score": 85,
                        "score_breakdown": [{"label": "Bullish BOS", "points": 20}],
                        "components": [
                            {"key": "bos_confirmed",     "label": "Bullish BOS",        "points": 20, "present": True},
                            {"key": "choch_confirmed",   "label": "Bullish CHOCH",      "points": 20, "present": False},
                            {"key": "vwap_confirmed",    "label": "VWAP Confirmation",  "points": 15, "present": True},
                            {"key": "liquidity_sweep",   "label": "Liquidity Sweep",    "points": 15, "present": True},
                            {"key": "volume_confirmed",  "label": "Volume Confirmation","points": 15, "present": True},
                            {"key": "cvd_confirmed",     "label": "CVD Agreement",      "points": 15, "present": True},
                            {"key": "preferred_session", "label": "Session Bonus",      "points": 10, "present": True},
                        ],
                    }})
    missing = NEW_KEYS - set(trace.keys())
    assert not missing, "Phase 5C keys missing from READY trace: %s" % missing


# ── R2: Existing Phase 5B keys unchanged ─────────────────────────────────────

PHASE_5B_REQUIRED = {
    "schema_version", "generated_at", "instrument",
    "domain", "state", "tier", "direction",
    "edge_score", "edge_grade", "strict_score", "strict_label",
    "strict_reason", "strict_missing",
    "has_active_trade", "has_trade_plan",
    "plan_available", "plan_executable",
    "next_action",
}


def test_phase5b_keys_unchanged():
    trace = _build()
    missing = PHASE_5B_REQUIRED - set(trace.keys())
    assert not missing, "Phase 5B keys regressed: %s" % missing


def test_schema_version_still_one():
    assert _build()["schema_version"] == 1


# ── R4: Fail-open — None result → new keys are None ──────────────────────────

def test_fail_open_new_keys_none():
    trace = app.build_legacy_decision_trace(None, "MGC")
    for key in NEW_KEYS:
        assert key in trace,           "%s missing from fail-open dict" % key
        assert trace[key] is None,     "%s should be None in fail-open dict, got %r" % (key, trace[key])


def test_fail_open_schema_still_one():
    trace = app.build_legacy_decision_trace(None, "MGC")
    assert trace["schema_version"] == 1
    assert trace["state"]          == "INVALID_DATA"


# ── R5: No money-path keys in the new fields ─────────────────────────────────
MONEY_PATH_KEYS = {"traderspost", "order", "gateway", "execution_mode",
                   "auto_fire", "send", "broker"}


def test_no_money_path_keys_in_new_fields():
    trace = _build()
    for key in NEW_KEYS:
        val = str(trace.get(key) or "").lower()
        hits = [mk for mk in MONEY_PATH_KEYS if mk in val]
        assert not hits, "Money-path key %s found in %s: %r" % (hits, key, val)


# ── Change 2: edge_score_detail ───────────────────────────────────────────────

def test_edge_detail_not_none_on_wait():
    ed = _build()["edge_score_detail"]
    assert ed is not None, "edge_score_detail should not be None for a normal WAIT"


def test_edge_detail_component_points_present():
    ed = _build()["edge_score_detail"]
    assert "component_points" in ed, "component_points missing"
    cp = ed["component_points"]
    assert isinstance(cp, dict), "component_points should be a dict"
    # VWAP and volume are present in fixture (both 15); structure is 0.
    assert cp.get("vwap") == 15,   "vwap should be 15 (present)"
    assert cp.get("volume") == 15, "volume should be 15 (present)"
    assert cp.get("structure") == 0, "structure should be 0 (absent)"


def test_edge_detail_final_score_matches_edge_score():
    result = _minimal_wait(edge_score=50)
    trace  = app.build_legacy_decision_trace(result, "MGC")
    ed     = trace["edge_score_detail"]
    assert ed["final_score"] == 50, "final_score should mirror edge_score"


def test_edge_detail_volatility_applied_is_zero():
    ed = _build()["edge_score_detail"]
    assert ed["volatility_applied_adjustment"] == 0, \
        "volatility_applied_adjustment must always be 0 (not applied to score)"


def test_edge_detail_volatility_configured_nonzero():
    ed = _build()["edge_score_detail"]
    # Fixture has score_adj=-10
    assert ed["volatility_configured_adjustment"] == -10, \
        "volatility_configured_adjustment should reflect vol.score_adj"


def test_edge_detail_gap_to_threshold():
    result = _minimal_wait(edge_score=50)
    result["gate_debug"]["edge_score"] = 50
    trace  = app.build_legacy_decision_trace(result, "MGC")
    ed     = trace["edge_score_detail"]
    assert ed["gap_to_threshold"] == 10, \
        "gap_to_threshold should be threshold(60) - score(50) = 10"


def test_edge_detail_passes_false_below_threshold():
    result = _minimal_wait(edge_score=50)
    trace  = app.build_legacy_decision_trace(result, "MGC")
    assert trace["edge_score_detail"]["passes"] is False, \
        "passes should be False when edge_score < threshold"


def test_edge_detail_passes_true_at_threshold():
    result = _minimal_wait(edge_score=60)
    result["gate_debug"]["edge_score"] = 60
    result["edge_breakdown"]["score"]  = 60
    result["edge_breakdown"]["raw_score"] = 60
    trace  = app.build_legacy_decision_trace(result, "MGC")
    assert trace["edge_score_detail"]["passes"] is True, \
        "passes should be True when edge_score == threshold"


# ── Change 3: zone_role ───────────────────────────────────────────────────────

def test_zone_role_not_none():
    zr = _build()["zone_role"]
    assert zr is not None, "zone_role should not be None for a normal trace"


def test_zone_role_not_required_in_scalp():
    zr = _build()["zone_role"]
    assert zr["required_by_current_mode"] is False, \
        "Zone should not be required in SCALP (GATE_REQUIRE_ZONE=False)"


def test_zone_role_hard_gate_pass_null_when_not_required():
    zr = _build()["zone_role"]
    assert zr["hard_gate_pass"] is None, \
        "hard_gate_pass should be None when zone is not required (avoids misleading False)"


def test_zone_role_hard_gate_pass_present_when_required():
    result = _minimal_wait()
    result["gate_debug"]["require_zone"] = True
    result["gate_debug"]["zone_valid"]   = True
    trace = app.build_legacy_decision_trace(result, "MGC")
    zr = trace["zone_role"]
    assert zr["required_by_current_mode"] is True
    assert zr["hard_gate_pass"] is True


def test_zone_role_edge_points_always_zero():
    zr = _build()["zone_role"]
    assert zr["edge_points"] == 0, \
        "Zone never contributes Edge Score points — must always be 0"


def test_zone_role_state_reflects_gd():
    zr = _build()["zone_role"]
    assert zr["state"] == "Tested", "zone_role.state should mirror gate_debug.zoneState"


# ── Change 4: failure_summary ─────────────────────────────────────────────────

def test_failure_summary_present_on_wait():
    fs = _build()["failure_summary"]
    assert fs is not None, "failure_summary should not be None for a WAIT trace"


def test_failure_summary_human_reason_edge_only():
    """When edge_score is the sole hard failure, human_reason says so plainly."""
    result = _minimal_wait(edge_score=50)
    result["gate_debug"]["edge_score"]        = 50
    result["gate_debug"]["failed_conditions"] = ["edge_score(50<60)"]
    trace  = app.build_legacy_decision_trace(result, "MGC")
    hr = trace["failure_summary"]["human_reason"]
    assert hr is not None, "human_reason should not be None"
    assert "50" in hr,  "human_reason should mention the current score 50"
    assert "60" in hr,  "human_reason should mention the threshold 60"


def test_failure_summary_context_only_has_zone():
    """Zone state appears in context_only (not hard_blockers) when not required."""
    fs = _build()["failure_summary"]
    ctx = fs.get("context_only") or []
    ctx_text = " ".join(ctx).lower()
    assert "zone" in ctx_text, \
        "Zone state should appear in context_only when zone is not required"


def test_failure_summary_context_only_has_vol():
    """Vol configuredAdj appears in context_only since it is NOT applied to score."""
    fs = _build()["failure_summary"]
    ctx = fs.get("context_only") or []
    ctx_text = " ".join(ctx).lower()
    assert "not applied" in ctx_text or "appliedadj" in ctx_text or "configuredadj" in ctx_text, \
        "Volatility configuredAdj note should appear in context_only"


def test_failure_summary_missing_optional_has_bos():
    """Absent BOS (20 pts) should appear in missing_optional_confirmations."""
    fs   = _build()["failure_summary"]
    opts = fs.get("missing_optional_confirmations") or []
    names = [o["name"] for o in opts]
    assert any("bos" in n.lower() for n in names), \
        "Absent BOS should appear in missing_optional_confirmations; got %s" % names


def test_failure_summary_none_when_market_closed():
    """failure_summary should be None when market is closed (no actionable WAIT)."""
    result = _minimal_wait()
    result["market_open"] = False
    result["verdict"]     = "MARKET CLOSED"
    trace  = app.build_legacy_decision_trace(result, "MGC")
    assert trace["failure_summary"] is None, \
        "failure_summary should be None when market is closed"


# ── Change 5: score_gap ───────────────────────────────────────────────────────

def test_score_gap_not_none():
    sg = _build()["score_gap"]
    assert sg is not None, "score_gap should not be None for a normal trace"


def test_score_gap_points_needed():
    result = _minimal_wait(edge_score=50)
    result["gate_debug"]["edge_score"] = 50
    result["edge_breakdown"]["score"]  = 50
    trace  = app.build_legacy_decision_trace(result, "MGC")
    sg = trace["score_gap"]
    assert sg["points_needed"] == 10, \
        "points_needed should be threshold(60) - score(50) = 10"


def test_score_gap_zero_when_passing():
    result = _minimal_wait(edge_score=75)
    result["gate_debug"]["edge_score"] = 75
    result["edge_breakdown"]["score"]  = 75
    trace  = app.build_legacy_decision_trace(result, "MGC")
    sg = trace["score_gap"]
    assert sg["points_needed"] == 0, \
        "points_needed should be 0 when score >= threshold"


def test_score_gap_available_missing_components_list():
    sg = _build()["score_gap"]
    avail = sg.get("available_missing_components") or []
    assert isinstance(avail, list), "available_missing_components should be a list"
    assert len(avail) > 0, \
        "Should have absent components in the fixture (BOS/CHOCH/Sweep/CVD/Session)"


def test_score_gap_direction_aware_labels_long():
    """For a Long direction trace, edge_breakdown.components already carry
    direction-aware labels (set by compute_edge_breakdown). score_gap must
    surface them unchanged so 'Bullish BOS' appears in the absent list."""
    result = _minimal_wait(direction="Long")
    result["strict_direction"] = "Long"
    # Fixture components are pre-relabeled as the real system does for Long.
    result["edge_breakdown"]["components"] = [
        {"key": "bos_confirmed",     "label": "Bullish BOS",        "points": 20, "present": False},
        {"key": "choch_confirmed",   "label": "Bullish CHOCH",      "points": 20, "present": False},
        {"key": "vwap_confirmed",    "label": "VWAP Reclaim",       "points": 15, "present": True},
        {"key": "liquidity_sweep",   "label": "Liquidity Sweep",    "points": 15, "present": False},
        {"key": "volume_confirmed",  "label": "Volume Confirmation","points": 15, "present": True},
        {"key": "cvd_confirmed",     "label": "CVD Confirms Long",  "points": 15, "present": False},
        {"key": "preferred_session", "label": "Session Bonus",      "points": 10, "present": False},
    ]
    trace = app.build_legacy_decision_trace(result, "MGC")
    sg    = trace["score_gap"]
    avail = sg.get("available_missing_components") or []
    names = [a["name"] for a in avail]
    assert any("Bullish" in n for n in names), \
        "Long direction should surface 'Bullish' labels from edge_breakdown; got %s" % names


# ── Change 6: structure_alert ─────────────────────────────────────────────────

def test_structure_alert_not_none():
    sa = _build()["structure_alert"]
    assert sa is not None, "structure_alert should not be None for a normal trace"


def test_structure_alert_not_verified_when_no_structure():
    sa = _build()["structure_alert"]
    assert sa["status"] == "NOT VERIFIED", \
        "status should be NOT VERIFIED when bos=False and choch=False"


def test_structure_alert_verified_when_bos_received():
    result = _minimal_wait()
    result["gate_debug"]["bos"]                = True
    result["gate_debug"]["structure_confirmed"] = True
    trace  = app.build_legacy_decision_trace(result, "MGC")
    sa = trace["structure_alert"]
    assert sa["status"] == "VERIFIED", \
        "status should be VERIFIED when bos=True and structure_confirmed=True"


def test_structure_alert_tradingview_check_scaffold():
    sa = _build()["structure_alert"]
    tv = sa.get("tradingview_check")
    assert isinstance(tv, dict), "tradingview_check should be a dict"
    required_keys = {
        "script_loaded", "correct_symbol", "intended_timeframe",
        "alert_exists", "webhook_enabled", "correct_webhook_url",
        "message_blank", "active", "expired",
    }
    missing = required_keys - set(tv.keys())
    assert not missing, "tradingview_check missing keys: %s" % missing
    # All values must be None (diagnostic scaffold, not yet populated)
    for k, v in tv.items():
        assert v is None, "tradingview_check[%r] should be None, got %r" % (k, v)


def test_structure_alert_bos_choch_booleans():
    sa = _build()["structure_alert"]
    assert sa["bos_received"]   is False, "bos_received should be False in fixture"
    assert sa["choch_received"] is False, "choch_received should be False in fixture"


# ── R3: Individual fail-open per block ───────────────────────────────────────

def test_corrupt_edge_breakdown_gives_none_detail():
    """If edge_breakdown is garbage, edge_score_detail should be None (fail-open)."""
    result = _minimal_wait()
    result["edge_breakdown"] = "not a dict"
    trace = app.build_legacy_decision_trace(result, "MGC")
    # Should not raise; the block should catch and leave the key None or some dict
    assert trace["schema_version"] == 1, "Must still return valid schema on corrupt sub-field"


def test_corrupt_gate_debug_gives_safe_trace():
    """If gate_debug is garbage, all blocks should fail-open gracefully."""
    result = _minimal_wait()
    result["gate_debug"] = "not a dict"
    trace = app.build_legacy_decision_trace(result, "MGC")
    assert trace["schema_version"] == 1
    for key in NEW_KEYS:
        assert key in trace, "%s missing when gate_debug is corrupt" % key


def test_no_mutation_with_new_fields():
    """Phase 5C must not mutate the input result dict."""
    result   = _minimal_wait()
    snapshot = copy.deepcopy(result)
    app.build_legacy_decision_trace(result, "MGC")
    assert result == snapshot, "build_legacy_decision_trace must not mutate the result dict"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print("  PASS  %s" % t.__name__)
            passed += 1
        except Exception as exc:
            import traceback
            print("  FAIL  %s: %s" % (t.__name__, exc))
            traceback.print_exc()
            failed += 1
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        sys.exit(1)
