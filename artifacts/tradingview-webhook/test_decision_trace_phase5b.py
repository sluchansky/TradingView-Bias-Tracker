"""test_decision_trace_phase5b.py — Phase 5B: build_legacy_decision_trace() tests.

Covers:
  T1.  Function exists and is importable.
  T2.  Returns all required schema keys.
  T3.  schema_version == 1.
  T4.  Flag OFF  → /decision-trace returns {enabled: false, traces: {}}.
  T5.  Flag OFF  → result dict from full_analysis has NO "decision_trace" key.
  T6.  LONG READY verdict  → domain=ENTRY, state=READY, tier=FULL, direction=Long.
  T7.  SHORT READY verdict → domain=ENTRY, state=READY, tier=FULL, direction=Short.
  T8.  LONG EARLY READY   → domain=ENTRY, state=EARLY, tier=EARLY, direction=Long.
  T9.  WAIT with direction → state=SETUP_FORMING, domain=WAIT.
  T10. WAIT no direction   → state=MONITOR, domain=WAIT.
  T11. MARKET CLOSED       → state=MARKET_CLOSED, domain=WAIT, tier=None.
  T12. Active trade present → domain=MANAGEMENT, state=MANAGE.
  T13. Edge fields forwarded correctly.
  T14. strict_reason forwarded when WAIT.
  T15. strict_missing forwarded when WAIT.
  T16. has_trade_plan True when trade_plan dict has trade_plan==True.
  T17. has_trade_plan False when trade_plan is absent or False.
  T18. Fail-open: broken result returns schema_version==1 + state==INVALID_DATA.
  T19. generated_at override is respected.
  T20. Function does NOT mutate the input result dict.
  T21. Flag ON  → cache populated (_LAST_DECISION_TRACE updated).
  T22. next_action read from main_brain.decision.next_action when present.
  T23. next_action is None when main_brain absent.
  T24. /decision-trace endpoint exists in app routes.

No mocking of scoring, gate, or execution logic.  build_legacy_decision_trace()
is tested directly; full_analysis() is called only for T5/T21 side-effect checks.
"""
import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(__file__))
import app
importlib.reload(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_wait_result(**overrides):
    """Minimal result dict representing a WAIT setup (no active trade)."""
    base = {
        "verdict":          "WAIT",
        "strict_reason":    "Missing: structure",
        "strict_label":     "WAIT",
        "strict_direction": None,
        "strict_score":     0,
        "strict_missing":   ["structure"],
        "edge_score":       30,
        "edge_grade":       None,
        "trade_plan":       {"trade_plan": False, "reason": "WAIT"},
        "active_trade":     None,
        "market_open":      True,
        "main_brain":       None,
    }
    base.update(overrides)
    return base


def _build_trace(result_overrides=None, instrument="MGC", generated_at=None):
    d = _minimal_wait_result(**(result_overrides or {}))
    return app.build_legacy_decision_trace(d, instrument, generated_at=generated_at)


# ── T1: function importable ───────────────────────────────────────────────────

def test_function_exists():
    assert callable(app.build_legacy_decision_trace), \
        "build_legacy_decision_trace is not callable"


# ── T2: all required schema keys present ─────────────────────────────────────

REQUIRED_KEYS = {
    "schema_version", "generated_at", "instrument",
    "domain", "state", "tier", "direction",
    "edge_score", "edge_grade", "strict_score", "strict_label",
    "strict_reason", "strict_missing",
    "has_active_trade", "has_trade_plan", "next_action",
}

def test_all_schema_keys_present():
    trace = _build_trace()
    missing = REQUIRED_KEYS - set(trace.keys())
    assert not missing, f"Missing schema keys: {missing}"


# ── T3: schema_version == 1 ───────────────────────────────────────────────────

def test_schema_version():
    assert _build_trace()["schema_version"] == 1


# ── T4: flag OFF → /decision-trace returns enabled=false ─────────────────────

def test_flag_off_endpoint_disabled():
    orig = app.DECISION_TRACE_SHADOW_ENABLED
    try:
        app.DECISION_TRACE_SHADOW_ENABLED = False
        with app.app.test_client() as c:
            resp = c.get("/decision-trace")
        import json
        body = json.loads(resp.data)
        assert body["enabled"] is False, f"enabled should be False, got {body}"
        assert body["traces"] == {}, f"traces should be empty, got {body}"
    finally:
        app.DECISION_TRACE_SHADOW_ENABLED = orig


# ── T5: flag OFF → full_analysis result has NO decision_trace key ─────────────

def test_flag_off_result_clean():
    orig = app.DECISION_TRACE_SHADOW_ENABLED
    try:
        app.DECISION_TRACE_SHADOW_ENABLED = False
        result = app.full_analysis()
        assert "decision_trace" not in result, \
            "decision_trace should not appear in result when flag is OFF"
    finally:
        app.DECISION_TRACE_SHADOW_ENABLED = orig


# ── T6: LONG READY → ENTRY / READY / FULL / Long ─────────────────────────────

def test_long_ready():
    trace = _build_trace({
        "verdict":          "LONG READY",
        "strict_direction": "Long",
        "edge_score":       85,
        "trade_plan":       {"trade_plan": True, "direction": "Long"},
        "market_open":      True,
    })
    assert trace["domain"]    == "ENTRY",  f"domain={trace['domain']}"
    assert trace["state"]     == "READY",  f"state={trace['state']}"
    assert trace["tier"]      == "FULL",   f"tier={trace['tier']}"
    assert trace["direction"] == "Long",   f"direction={trace['direction']}"


# ── T7: SHORT READY → ENTRY / READY / FULL / Short ───────────────────────────

def test_short_ready():
    trace = _build_trace({
        "verdict":          "SHORT READY",
        "strict_direction": "Short",
        "edge_score":       82,
        "trade_plan":       {"trade_plan": True, "direction": "Short"},
        "market_open":      True,
    })
    assert trace["domain"]    == "ENTRY"
    assert trace["state"]     == "READY"
    assert trace["tier"]      == "FULL"
    assert trace["direction"] == "Short"


# ── T8: LONG EARLY READY → ENTRY / EARLY / EARLY / Long ──────────────────────

def test_long_early_ready():
    trace = _build_trace({
        "verdict":          "LONG EARLY READY",
        "strict_direction": "Long",
        "edge_score":       56,
        "trade_plan":       {"trade_plan": True, "direction": "Long"},
        "market_open":      True,
    })
    assert trace["domain"]    == "ENTRY"
    assert trace["state"]     == "EARLY"
    assert trace["tier"]      == "EARLY"
    assert trace["direction"] == "Long"


# ── T9: WAIT with strict_direction → SETUP_FORMING ───────────────────────────

def test_wait_with_direction():
    trace = _build_trace({"verdict": "WAIT", "strict_direction": "Long"})
    assert trace["state"]  == "SETUP_FORMING", f"state={trace['state']}"
    assert trace["domain"] == "WAIT"
    assert trace["tier"]   is None


# ── T10: WAIT no direction → MONITOR ─────────────────────────────────────────

def test_wait_no_direction():
    trace = _build_trace({"verdict": "WAIT", "strict_direction": None})
    assert trace["state"]  == "MONITOR",  f"state={trace['state']}"
    assert trace["domain"] == "WAIT"
    assert trace["tier"]   is None


# ── T11: MARKET CLOSED → MARKET_CLOSED / WAIT / tier=None ────────────────────

def test_market_closed():
    trace = _build_trace({
        "verdict":     "MARKET CLOSED",
        "market_open": False,
        "edge_score":  0,
    })
    assert trace["state"]  == "MARKET_CLOSED", f"state={trace['state']}"
    assert trace["domain"] == "WAIT"
    assert trace["tier"]   is None


def test_market_closed_via_market_open_false():
    trace = _build_trace({
        "verdict":          "WAIT",
        "market_open":      False,
        "strict_direction": "Long",   # direction present but market closed → CLOSED wins
    })
    assert trace["state"] == "MARKET_CLOSED"


# ── T12: active trade → MANAGEMENT / MANAGE ───────────────────────────────────

def test_active_trade_management():
    trace = _build_trace({
        "verdict":     "WAIT",
        "active_trade": {"direction": "Long", "entry": 2050.0},
        "market_open":  True,
    })
    assert trace["domain"] == "MANAGEMENT", f"domain={trace['domain']}"
    assert trace["state"]  == "MANAGE",     f"state={trace['state']}"
    assert trace["tier"]   is None


def test_active_trade_beats_actionable_verdict():
    trace = _build_trace({
        "verdict":          "LONG READY",
        "strict_direction": "Long",
        "active_trade":     {"direction": "Long", "entry": 2050.0},
        "market_open":      True,
    })
    assert trace["domain"] == "MANAGEMENT"
    assert trace["state"]  == "MANAGE"


# ── T13: edge fields forwarded correctly ─────────────────────────────────────

def test_edge_fields_forwarded():
    trace = _build_trace({
        "edge_score":   88,
        "edge_grade":   "A+",
        "strict_score": 88,
        "strict_label": "Strong Trade",
    })
    assert trace["edge_score"]   == 88,            f"edge_score={trace['edge_score']}"
    assert trace["edge_grade"]   == "A+",          f"edge_grade={trace['edge_grade']}"
    assert trace["strict_score"] == 88,            f"strict_score={trace['strict_score']}"
    assert trace["strict_label"] == "Strong Trade",f"strict_label={trace['strict_label']}"


# ── T14: strict_reason forwarded ─────────────────────────────────────────────

def test_strict_reason_forwarded():
    trace = _build_trace({"strict_reason": "Missing: CHOCH/BOS"})
    assert trace["strict_reason"] == "Missing: CHOCH/BOS"


# ── T15: strict_missing forwarded ────────────────────────────────────────────

def test_strict_missing_forwarded():
    trace = _build_trace({"strict_missing": ["structure", "vwap"]})
    assert trace["strict_missing"] == ["structure", "vwap"]


# ── T16: has_trade_plan True when trade_plan.trade_plan == True ───────────────

def test_has_trade_plan_true():
    trace = _build_trace({
        "trade_plan": {"trade_plan": True, "direction": "Long"},
    })
    assert trace["has_trade_plan"] is True


# ── T17: has_trade_plan False when absent or False ───────────────────────────

def test_has_trade_plan_false_when_missing():
    trace = _build_trace({"trade_plan": None})
    assert trace["has_trade_plan"] is False


def test_has_trade_plan_false_when_flag_false():
    trace = _build_trace({"trade_plan": {"trade_plan": False}})
    assert trace["has_trade_plan"] is False


# ── T18: fail-open: broken result → INVALID_DATA ─────────────────────────────

def test_fail_open_broken_result():
    trace = app.build_legacy_decision_trace(None, "MGC")
    assert trace["schema_version"] == 1
    assert trace["state"]          == "INVALID_DATA"
    assert trace["instrument"]     == "MGC"


def test_fail_open_non_dict():
    trace = app.build_legacy_decision_trace("broken", "MNQ")
    assert trace["schema_version"] == 1
    assert trace["state"]          == "INVALID_DATA"


# ── T19: generated_at override respected ──────────────────────────────────────

def test_generated_at_override():
    ts = "2025-01-01T00:00:00Z"
    trace = _build_trace(generated_at=ts)
    assert trace["generated_at"] == ts


def test_generated_at_default_is_iso():
    trace = _build_trace()
    ts = trace["generated_at"]
    assert ts and "T" in ts and ts.endswith("Z"), f"generated_at not ISO: {ts!r}"


# ── T20: function does NOT mutate the input result ────────────────────────────

def test_no_mutation():
    result = _minimal_wait_result()
    import copy
    snapshot = copy.deepcopy(result)
    app.build_legacy_decision_trace(result, "MGC")
    assert result == snapshot, "build_legacy_decision_trace mutated the result dict"


# ── T21: flag ON → _LAST_DECISION_TRACE updated after full_analysis ───────────

def test_flag_on_cache_populated():
    orig = app.DECISION_TRACE_SHADOW_ENABLED
    try:
        app.DECISION_TRACE_SHADOW_ENABLED = True
        with app._DECISION_TRACE_LOCK:
            app._LAST_DECISION_TRACE.clear()
        app.full_analysis()
        with app._DECISION_TRACE_LOCK:
            snap = dict(app._LAST_DECISION_TRACE)
        assert snap, "Cache should have at least one entry when flag is ON"
        inst = next(iter(snap))
        trace = snap[inst]
        assert trace.get("schema_version") == 1
        assert "state" in trace
    finally:
        app.DECISION_TRACE_SHADOW_ENABLED = orig
        with app._DECISION_TRACE_LOCK:
            app._LAST_DECISION_TRACE.clear()


# ── T22: next_action from main_brain.decision ────────────────────────────────

def test_next_action_from_main_brain():
    trace = _build_trace({
        "main_brain": {"decision": {"next_action": "Wait for structure"}},
    })
    assert trace["next_action"] == "Wait for structure"


# ── T23: next_action None when main_brain absent ─────────────────────────────

def test_next_action_none_when_absent():
    trace = _build_trace({"main_brain": None})
    assert trace["next_action"] is None


def test_next_action_none_when_decision_absent():
    trace = _build_trace({"main_brain": {}})
    assert trace["next_action"] is None


# ── T24: /decision-trace endpoint registered in Flask app ────────────────────

def test_endpoint_registered():
    rules = [str(r) for r in app.app.url_map.iter_rules()]
    assert "/decision-trace" in rules, \
        f"/decision-trace not in Flask routes: {rules}"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            import traceback
            print(f"  FAIL  {t.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
