"""test_brain_contract.py — Phase 2 Brain Output Contract tests.

Covers requirements A–O from the task spec:
  A. brain exists in /status
  B. contract_version == "1.0"
  C. brain.instrument == active_ticker
  D. brain.score.value == top-level edge_score
  E. brain.score.source == "EDGE_COMPONENTS"
  F. score components reconcile to score including adjustments
  G. WAIT verdict produces direction=null
  H. WAIT verdict produces trade_plan=null
  I. actionable verdict preserves the existing trade plan
  J. reasons contain at most 3 unique non-empty strings
  K. risks contain at most 3 unique non-empty strings
  L. legacy_confidence and strict_score are absent from brain
  M. directional values are per-instrument
  N. no second full_analysis() call is introduced
  O. all existing confidence-integrity tests still pass

No mocking of scoring, gate, or execution logic.  Tests use the real
full_analysis() pipeline via _build_status_payload() so the contract is
validated end-to-end.
"""
import sys
import os
import importlib
import traceback

sys.path.insert(0, os.path.dirname(__file__))
import app
importlib.reload(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status(ticker=None):
    """Return the full _build_status_payload dict for one instrument."""
    return app._build_status_payload(ticker)


def _brain(ticker=None):
    """Return the brain sub-dict from a freshly-built /status payload."""
    return _status(ticker)["brain"]


# ── A: brain key present in /status ──────────────────────────────────────────

def test_brain_key_in_status():
    p = _status()
    assert "brain" in p, "brain key missing from /status payload"
    assert isinstance(p["brain"], dict), \
        f"brain must be a dict, got {type(p['brain'])}"


# ── B: contract_version == "1.0" ─────────────────────────────────────────────

def test_contract_version():
    b = _brain()
    assert b.get("contract_version") == "1.0", \
        f"contract_version {b.get('contract_version')!r} != '1.0'"


# ── C: brain.instrument == active_ticker ─────────────────────────────────────

def test_instrument_matches_active_ticker():
    p = _status()
    b = p["brain"]
    assert b.get("instrument") == p.get("active_ticker"), (
        f"brain.instrument {b.get('instrument')!r} != "
        f"active_ticker {p.get('active_ticker')!r}"
    )


# ── D: brain.score.value == top-level edge_score ─────────────────────────────

def test_score_value_matches_top_level():
    p = _status()
    b = p["brain"]
    assert b["score"]["value"] == p["edge_score"], (
        f"brain.score.value {b['score']['value']} "
        f"!= edge_score {p['edge_score']}"
    )


# ── E: brain.score.source == "EDGE_COMPONENTS" ───────────────────────────────

def test_score_source_constant():
    b = _brain()
    assert b["score"]["source"] == "EDGE_COMPONENTS", \
        f"score.source {b['score']['source']!r} != 'EDGE_COMPONENTS'"


# ── F: score components reconcile to score including adjustments ──────────────
# Reconciliation formula (when no positive learning delta — default flag-OFF):
#   sum(points for present components) + sum(points for adjustments)
#   clamped to [0, EDGE_SCORE_MAX] == score.value
# In the test environment all engine flags are OFF so learning delta is zero,
# making the component sum the sole contributor.

def test_score_components_reconcile():
    b = _brain()
    score = b["score"]["value"]
    comps = b["score"]["components"]
    adjs  = b["score"]["adjustments"]

    # Structural validity — each component must have the four required keys.
    for c in comps:
        assert isinstance(c.get("key"), str),  f"component missing key: {c}"
        assert isinstance(c.get("label"), str), f"component missing label: {c}"
        assert isinstance(c.get("points"), (int, float)) and c["points"] >= 0, \
            f"component points invalid: {c}"
        assert isinstance(c.get("present"), bool), \
            f"component present not bool: {c}"

    # Adjustments must have label + numeric points.
    for adj in adjs:
        assert isinstance(adj.get("label"), str), \
            f"adjustment missing label: {adj}"
        assert isinstance(adj.get("points"), (int, float)), \
            f"adjustment points invalid: {adj}"
        assert adj["points"] < 0, \
            f"adjustment points should be negative: {adj}"

    # Reconcile: present-component sum + adjustment sum, clamped.
    comp_total = sum(
        c["points"] for c in comps
        if c.get("present") and isinstance(c.get("points"), (int, float))
    )
    adj_total = sum(
        adj["points"] for adj in adjs
        if isinstance(adj.get("points"), (int, float))
    )
    expected = max(0, min(app.EDGE_SCORE_MAX, comp_total + adj_total))
    assert score == expected, (
        f"Brain score {score} != recomputed {expected} "
        f"(comp_total={comp_total}, adj_total={adj_total})"
    )


# ── G: WAIT verdict → direction=null ─────────────────────────────────────────

def test_wait_verdict_direction_null():
    p = _status()
    b = p["brain"]
    verdict = b["decision"]["verdict"]
    if not app.is_actionable(verdict):
        assert b["decision"]["direction"] is None, (
            f"direction should be None for non-actionable verdict "
            f"{verdict!r}, got {b['decision']['direction']!r}"
        )


# ── H: WAIT verdict → trade_plan=null ────────────────────────────────────────

def test_wait_verdict_trade_plan_null():
    p = _status()
    b = p["brain"]
    verdict = b["decision"]["verdict"]
    if not app.is_actionable(verdict):
        assert b["trade_plan"] is None, (
            f"trade_plan should be None for non-actionable verdict {verdict!r}"
        )


# ── I: actionable verdict preserves the existing trade plan ──────────────────
# Inject a minimal fake result dict directly into _build_brain_contract so
# the test is not time-dependent (no live signal needed).

def test_actionable_verdict_preserves_trade_plan():
    fake_plan = {
        "direction": "Long",
        "entry": 3000.0,
        "stop":  2990.0,
        "target1": 3030.0,
        "target2": 3060.0,
        "rr_num": 3.0,
    }
    fake_result = {
        "verdict":    "LONG READY",
        "edge_score": 70,
        "edge_grade": "A",
        "edge_breakdown": {
            "score": 70,
            "grade": "A",
            "score_breakdown": [
                {"label": "BOS Confirmed",   "points": 20},
                {"label": "CHOCH Confirmed", "points": 20},
                {"label": "VWAP Reclaim",    "points": 15},
                {"label": "Liquidity Sweep", "points": 15},
            ],
            "risk_adjustments": [],
            "components": [
                {"key": "bos_confirmed",  "label": "BOS Confirmed",
                 "points": 20, "present": True},
                {"key": "choch_confirmed","label": "CHOCH Confirmed",
                 "points": 20, "present": True},
                {"key": "vwap_confirmed", "label": "VWAP Reclaim",
                 "points": 15, "present": True},
                {"key": "liquidity_sweep","label": "Liquidity Sweep",
                 "points": 15, "present": True},
                {"key": "volume_confirmed","label": "Volume Confirmation",
                 "points": 15, "present": False},
                {"key": "cvd_confirmed",  "label": "CVD Agreement",
                 "points": 15, "present": False},
                {"key": "preferred_session","label": "Session Bonus",
                 "points": 10, "present": False},
            ],
            "reasons": ["BOS Confirmed", "CHOCH Confirmed",
                        "VWAP Reclaim",  "Liquidity Sweep"],
            "risks": [],
        },
        "active_ticker":        "MGC",
        "stage_next_step":      "Execute setup",
        "trade_plan":           fake_plan,
        "market_intelligence":  None,
        "confidence_governor":  None,
        "last_valid_time":      None,
        "conviction_tier":      "A",
    }
    b = app._build_brain_contract(fake_result, "2026-01-01T00:00:00Z")
    assert b["decision"]["is_ready"] is True, \
        f"is_ready should be True for LONG READY verdict"
    assert b["decision"]["direction"] == "Long", \
        f"direction should be 'Long' for LONG READY, got {b['decision']['direction']!r}"
    assert b["trade_plan"] is not None, \
        "trade_plan should not be None for an actionable verdict"
    assert b["trade_plan"]["direction"] == "Long", \
        f"trade_plan.direction mismatch: {b['trade_plan']['direction']!r}"
    assert b["trade_plan"]["entry"] == 3000.0


# ── J: reasons ≤ 3 unique non-empty strings ──────────────────────────────────

def test_reasons_at_most_3_unique_non_empty():
    b = _brain()
    top = b["reasons"]["top"]
    assert isinstance(top, list), f"reasons.top should be a list, got {type(top)}"
    assert len(top) <= 3, f"reasons.top has {len(top)} items (max 3)"
    for r in top:
        assert isinstance(r, str) and r.strip(), \
            f"empty or non-string reason found: {r!r}"
    assert len(set(top)) == len(top), \
        f"duplicate reasons detected: {top}"


# ── K: risks ≤ 3 unique non-empty strings ────────────────────────────────────

def test_risks_at_most_3_unique_non_empty():
    b = _brain()
    top = b["risks"]["top"]
    assert isinstance(top, list), f"risks.top should be a list, got {type(top)}"
    assert len(top) <= 3, f"risks.top has {len(top)} items (max 3)"
    for r in top:
        assert isinstance(r, str) and r.strip(), \
            f"empty or non-string risk found: {r!r}"
    assert len(set(top)) == len(top), \
        f"duplicate risks detected: {top}"


# ── L: legacy_confidence and strict_score absent from brain ──────────────────

def test_forbidden_fields_absent_from_brain():
    b = _brain()
    FORBIDDEN = ("legacy_confidence", "strict_score", "legacy_confidence_valid",
                 "bullish_score", "bearish_score", "bias")
    for field in FORBIDDEN:
        assert field not in b, \
            f"forbidden field {field!r} found in brain top-level"
    # Also check one level deep in the sub-dicts that consumers might read.
    for sub_key in ("decision", "score", "directional", "supporting_diagnostics",
                    "reasons", "risks", "freshness"):
        sub = b.get(sub_key) or {}
        for field in ("legacy_confidence", "strict_score"):
            assert field not in sub, \
                f"forbidden field {field!r} found in brain.{sub_key}"


# ── M: directional values come from market_intelligence, not the cross-instrument bias ──

def test_directional_from_market_intelligence():
    p = _status()
    b = p["brain"]
    dc = b.get("directional") or {}

    # Required keys present.
    for key in ("bias", "long", "short", "margin"):
        assert key in dc, f"directional missing key {key!r}"

    # margin = abs(long - short).
    _l = float(dc.get("long")  or 0)
    _s = float(dc.get("short") or 0)
    assert abs(dc["margin"] - abs(_l - _s)) < 1e-6, (
        f"directional.margin {dc['margin']} != abs({_l} - {_s}) = {abs(_l - _s)}"
    )

    # The directional block must NOT be the cross-instrument top-level bias
    # (which is a string like "Bullish"/"Bearish"/"Neutral").  The MI-sourced
    # directional block has numeric long/short fields.
    assert isinstance(dc["long"],  (int, float)), \
        f"directional.long should be numeric, got {type(dc['long'])}"
    assert isinstance(dc["short"], (int, float)), \
        f"directional.short should be numeric, got {type(dc['short'])}"


# ── N: no second full_analysis() call ────────────────────────────────────────

def test_no_second_full_analysis_call():
    call_count = {"n": 0}
    _original_fa = app.full_analysis

    def _counting_fa(*args, **kwargs):
        call_count["n"] += 1
        return _original_fa(*args, **kwargs)

    app.full_analysis = _counting_fa
    try:
        _status()
    finally:
        app.full_analysis = _original_fa

    assert call_count["n"] == 1, (
        f"full_analysis called {call_count['n']} times — expected exactly 1; "
        "brain contract must be assembled from the already-computed result"
    )


# ── O: all existing confidence-integrity tests still pass ────────────────────

def test_existing_confidence_integrity_passes():
    import test_confidence_integrity as ci
    importlib.reload(ci)
    ci.test_edge_components_weights_unchanged()
    ci.test_result_confidence_field_exists()
    ci.test_build_strict_trade_plan_receives_authoritative_edge_score()
    ci.test_status_payload_edge_score_trace()
    ci.test_status_payload_legacy_confidence()
    ci.test_active_ticker_per_instrument()
    ci.test_status_payload_edge_score_instrument_matches_ticker()


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    TESTS = [
        ("A", "brain_key_in_status",                  test_brain_key_in_status),
        ("B", "contract_version",                      test_contract_version),
        ("C", "instrument_matches_active_ticker",      test_instrument_matches_active_ticker),
        ("D", "score_value_matches_top_level",         test_score_value_matches_top_level),
        ("E", "score_source_constant",                 test_score_source_constant),
        ("F", "score_components_reconcile",            test_score_components_reconcile),
        ("G", "wait_verdict_direction_null",           test_wait_verdict_direction_null),
        ("H", "wait_verdict_trade_plan_null",          test_wait_verdict_trade_plan_null),
        ("I", "actionable_verdict_preserves_plan",     test_actionable_verdict_preserves_trade_plan),
        ("J", "reasons_at_most_3_unique_non_empty",    test_reasons_at_most_3_unique_non_empty),
        ("K", "risks_at_most_3_unique_non_empty",      test_risks_at_most_3_unique_non_empty),
        ("L", "forbidden_fields_absent_from_brain",    test_forbidden_fields_absent_from_brain),
        ("M", "directional_from_market_intelligence",  test_directional_from_market_intelligence),
        ("N", "no_second_full_analysis_call",          test_no_second_full_analysis_call),
        ("O", "existing_confidence_integrity_passes",  test_existing_confidence_integrity_passes),
    ]

    passed = failed = 0
    for letter, name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {letter}: test_{name}")
            passed += 1
        except Exception:
            print(f"FAIL  {letter}: test_{name}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed of {passed + failed}")
    if failed:
        sys.exit(1)
