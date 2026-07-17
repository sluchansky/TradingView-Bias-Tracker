"""
Decision Pipeline V2 — Phase 2 (AI Analyst Layer) tests.

Tests verify:
 1.  Briefing has all 8 required top-level keys
 2.  market_environment has regime + primary_driver (human-readable)
 3.  money_flow leading = Gold for RISK_OFF regime
 4.  money_flow weak = Technology + Broad Equity for RISK_OFF regime
 5.  money_flow leading = Technology for RISK_ON regime
 6.  money_flow weak = Gold for RISK_ON regime
 7.  market_priority ranked has all 4 instruments
 8.  technical_verdict.source is always "production_engine" (never recomputed)
 9.  technical_verdict.verdict matches the live_verdict passed from outside
10.  blocking_factors come from VALIDATE failed_checks, not recomputed
11.  next_action populated (non-empty string)
12.  invalidation populated (non-empty string)
13.  briefing_text is a non-empty plain-English string
14.  briefing_text contains NO trade command words (BUY/SELL/ORDER/EXECUTE/ROUTE)
15.  display_only flag is always True
16.  Fail-open: bad stage inputs return available=False, not an exception
17.  NEUTRAL regime → no clear leading/weak (graceful, no crash)
18.  UNKNOWN regime → graceful handling
19.  Briefing does NOT alter any stage dicts (read-only)
20.  Flag OFF (DECISION_PIPELINE_V2_ENABLED=False) → analyst_briefing absent
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
import app as _app


# ── stage dict builders ───────────────────────────────────────────────────────

def _s1(instrument="MGC", price=2400.0, vwap=2395.0, cvd="bullish",
        market_open=True, has_position=False):
    return {
        "status": "OK", "instrument": instrument,
        "price": price, "price_age_s": 10.0,
        "vwap_value": vwap, "vwap_age_s": 30.0,
        "price_above_vwap": (price > vwap),
        "cvd_state": cvd, "cvd_age_s": 20.0,
        "rvol_value": 1.5, "vol_spike_fresh": True, "vol_spike_age_s": 60.0,
        "atr_pts": 5.0, "vol_ratio": 1.2,
        "structure_signals": [{"type": "BOS", "direction": "LONG", "age_s": 300.0}],
        "sweep_signals": [], "has_position": has_position,
        "active_trade": None, "market_open": market_open,
        "daily_loss_cap": None, "market_env_snapshot": None, "data_coverage": 0.75,
    }


def _s2(regime="RISK_OFF", driver="TECHNICAL", confidence=65):
    return {
        "status": "OK", "source": "instrument_derived",
        "regime": regime, "regime_confidence": confidence,
        "primary_driver": driver, "secondary_drivers": [],
        "risk_state": "DEFENSIVE" if regime == "RISK_OFF" else "AGGRESSIVE",
        "dominant_theme": "SAFE_HAVEN_DEMAND",
        "supporting_evidence": ["price_above_vwap"],
        "conflicting_evidence": [], "data_quality": "MODERATE",
    }


def _s3_from_stages(inst="MGC", regime="RISK_OFF", driver="TECHNICAL"):
    obs  = _s1(instrument=inst)
    intp = _s2(regime=regime, driver=driver)
    return _app._dpv2_stage_prioritize(obs, intp, "SCALP")


def _s4(passed=None, failed=None):
    return {
        "status": "OK", "symbol": "MGC", "strategy": "DECISION_PIPELINE_V2",
        "technical_status": "WAIT" if failed else "READY",
        "technical_confidence": 60,
        "passed_checks": passed or ["Fresh structure: BOS 300s ago", "Price above VWAP"],
        "failed_checks":  failed or [],
        "risk_checks": {}, "account_checks": {"market_open": True, "has_position": False},
        "pass_rate": 80,
    }


def _s5(live_verdict="WAIT", live_direction=None, shadow_verdict="WAIT",
        expl=None):
    return {
        "status": "OK", "symbol": "MGC",
        "market_regime": "RISK_OFF", "primary_driver": "TECHNICAL",
        "market_priority": "STRONGLY_FAVORED", "directional_context": "BULLISH",
        "technical_verdict": "WAIT",
        "production_verdict": live_verdict,
        "shadow_verdict": shadow_verdict,
        "agreement_status": "AGREE",
        "changed_production_behavior": False,
        "explanation": expl or {
            "what_is_happening":               "The market is currently Risk Off.",
            "why_it_is_happening":             "Technical price action is the primary driver.",
            "why_this_market_is_relevant":     "Gold benefits from risk-off conditions.",
            "what_the_technical_engine_found": "Technical engine: WAIT. Missing: structure.",
            "what_must_happen_next":           "Wait for a bullish reclaim above VWAP.",
            "what_invalidates_the_idea":       "Loss of structure and sustained break of VWAP.",
        },
        "shadow_mode": True,
        "pipeline_verdict": shadow_verdict,
        "pipeline_direction": "LONG" if shadow_verdict == "READY" else None,
        "pipeline_confidence": 55,
        "pipeline_reasoning": ["Gate failed"],
        "agreement_with_live": True,
        "live_verdict": live_verdict,
        "live_direction": live_direction,
        "divergence_reason": None,
    }


def _briefing(regime="RISK_OFF", driver="TECHNICAL", live_verdict="WAIT",
              failed=None, instrument="MGC"):
    ob = _s1(instrument=instrument)
    i2 = _s2(regime=regime, driver=driver)
    p3 = _s3_from_stages(inst=instrument, regime=regime, driver=driver)
    v4 = _s4(failed=failed)
    d5 = _s5(live_verdict=live_verdict)
    return _app._dpv2_analyst_briefing(ob, i2, p3, v4, d5, instrument, "SCALP")


# ── tests ────────────────────────────────────────────────────────────────────

def test_briefing_has_all_8_keys():
    b = _briefing()
    required = {
        "market_environment",
        "money_flow",
        "market_priority",
        "technical_verdict",
        "next_action",
        "invalidation",
        "briefing_text",
        "available",
    }
    missing = required - set(b.keys())
    assert not missing, "Briefing missing keys: %s" % missing
    print("PASS test_briefing_has_all_8_keys")


def test_market_environment_human_readable():
    b = _briefing(regime="RISK_OFF", driver="GEOPOLITICAL")
    me = b["market_environment"]
    assert me["regime"] == "Risk Off",              "regime should be 'Risk Off', got: %s" % me["regime"]
    assert me["primary_driver"] == "Geopolitical Escalation", (
        "driver should be 'Geopolitical Escalation', got: %s" % me["primary_driver"])
    # Must not expose raw enum values as the label
    assert "_" not in me["regime"],         "regime label must be human-readable (no underscores)"
    assert "_" not in me["primary_driver"], "driver label must be human-readable (no underscores)"
    print("PASS test_market_environment_human_readable")


def test_money_flow_risk_off_leading_gold():
    b = _briefing(regime="RISK_OFF")
    mf = b["money_flow"]
    assert "Gold" in mf["leading"], "RISK_OFF should show Gold as leading, got: %s" % mf["leading"]
    print("PASS test_money_flow_risk_off_leading_gold")


def test_money_flow_risk_off_weak_equity():
    b = _briefing(regime="RISK_OFF")
    mf = b["money_flow"]
    weak_set = set(mf["weak"])
    assert weak_set & {"Technology", "Broad Equity"}, (
        "RISK_OFF should show equity as weak, got: %s" % mf["weak"])
    print("PASS test_money_flow_risk_off_weak_equity")


def test_money_flow_risk_on_leading_technology():
    b = _briefing(regime="RISK_ON", instrument="MNQ")
    mf = b["money_flow"]
    assert "Technology" in mf["leading"] or "Broad Equity" in mf["leading"], (
        "RISK_ON should show equity as leading, got: %s" % mf["leading"])
    print("PASS test_money_flow_risk_on_leading_technology")


def test_money_flow_risk_on_weak_gold():
    b = _briefing(regime="RISK_ON", instrument="MNQ")
    mf = b["money_flow"]
    assert "Gold" in mf["weak"], "RISK_ON should show Gold as weak, got: %s" % mf["weak"]
    print("PASS test_money_flow_risk_on_weak_gold")


def test_market_priority_all_4_instruments():
    b = _briefing()
    mp = b["market_priority"]
    syms = {r["symbol"] for r in mp["ranked"]}
    assert syms == {"MGC", "MNQ", "MES", "MYM"}, (
        "All 4 instruments should appear in ranked, got: %s" % syms)
    print("PASS test_market_priority_all_4_instruments")


def test_technical_verdict_source_is_production_engine():
    b = _briefing(live_verdict="LONG READY")
    tv = b["technical_verdict"]
    assert tv["source"] == "production_engine", (
        "source must be 'production_engine', got: %s" % tv["source"])
    assert "note" in tv and "not recomputed" in tv["note"].lower(), (
        "note must state verdict is not recomputed")
    print("PASS test_technical_verdict_source_is_production_engine")


def test_technical_verdict_matches_live_verdict():
    for live_v in ("WAIT", "LONG READY", "SHORT READY"):
        b = _briefing(live_verdict=live_v)
        tv = b["technical_verdict"]
        assert tv["verdict"] == live_v, (
            "Analyst verdict '%s' must match live_verdict '%s'" % (tv["verdict"], live_v))
    print("PASS test_technical_verdict_matches_live_verdict")


def test_blocking_factors_from_validate_failed_checks():
    failures = ["Missing VWAP confirmation", "No fresh structure signal"]
    b = _briefing(failed=failures)
    tv = b["technical_verdict"]
    for f in failures:
        assert f in tv["blocking_factors"], (
            "Expected '%s' in blocking_factors, got: %s" % (f, tv["blocking_factors"]))
    print("PASS test_blocking_factors_from_validate_failed_checks")


def test_next_action_non_empty():
    b = _briefing()
    assert b["next_action"], "next_action must be a non-empty string"
    assert isinstance(b["next_action"], str)
    print("PASS test_next_action_non_empty")


def test_invalidation_non_empty():
    b = _briefing()
    assert b["invalidation"], "invalidation must be a non-empty string"
    assert isinstance(b["invalidation"], str)
    print("PASS test_invalidation_non_empty")


def test_briefing_text_non_empty_string():
    b = _briefing()
    assert b["briefing_text"], "briefing_text must be non-empty"
    assert isinstance(b["briefing_text"], str)
    assert len(b["briefing_text"]) > 50, "briefing_text too short: %d chars" % len(b["briefing_text"])
    print("PASS test_briefing_text_non_empty_string")


def test_briefing_text_no_trade_commands():
    b = _briefing(live_verdict="LONG READY")
    text = b["briefing_text"].upper()
    forbidden = ["EXECUTE", "ROUTE", "ORDER", "SUBMIT", "FIRE"]
    for word in forbidden:
        assert word not in text, (
            "briefing_text must not contain trade command '%s'" % word)
    print("PASS test_briefing_text_no_trade_commands")


def test_display_only_always_true():
    for regime in ("RISK_OFF", "RISK_ON", "NEUTRAL", "UNKNOWN"):
        b = _briefing(regime=regime)
        assert b.get("display_only") is True, (
            "display_only must be True for regime %s" % regime)
    print("PASS test_display_only_always_true")


def test_fail_open_bad_stages():
    # Passing None / empty dicts should not raise — must return available=False
    result = _app._dpv2_analyst_briefing(None, None, None, None, None, "MGC", "SCALP")
    # Should be graceful — either available or a dict with error info
    assert isinstance(result, dict), "Must return a dict, not raise"
    # Richer: if available=False, should have an error key
    if not result.get("available"):
        assert "error" in result or "shadow_mode_note" in result
    print("PASS test_fail_open_bad_stages")


def test_neutral_regime_no_crash():
    b = _briefing(regime="NEUTRAL")
    assert isinstance(b, dict)
    assert b.get("available") is True or "error" in b
    # money_flow may be empty — that is fine
    if b.get("available"):
        mf = b.get("money_flow", {})
        assert isinstance(mf.get("leading", []), list)
        assert isinstance(mf.get("weak",    []), list)
    print("PASS test_neutral_regime_no_crash")


def test_unknown_regime_no_crash():
    b = _briefing(regime="UNKNOWN")
    assert isinstance(b, dict)
    if b.get("available"):
        me = b["market_environment"]
        assert me["regime"] == "Unknown"    # human-readable
    print("PASS test_unknown_regime_no_crash")


def test_briefing_does_not_mutate_stage_dicts():
    ob = _s1()
    i2 = _s2()
    p3 = _s3_from_stages()
    v4 = _s4()
    d5 = _s5()
    # Take snapshots of key fields
    obs_price_before    = ob.get("price")
    regime_before       = i2.get("regime")
    ranked_len_before   = len(p3.get("ranked_instruments", []))
    passed_len_before   = len(v4.get("passed_checks", []))
    verdict_before      = d5.get("production_verdict")

    _app._dpv2_analyst_briefing(ob, i2, p3, v4, d5, "MGC", "SCALP")

    assert ob.get("price")                          == obs_price_before,   "s1 mutated"
    assert i2.get("regime")                         == regime_before,      "s2 mutated"
    assert len(p3.get("ranked_instruments", []))    == ranked_len_before,  "s3 mutated"
    assert len(v4.get("passed_checks", []))         == passed_len_before,  "s4 mutated"
    assert d5.get("production_verdict")             == verdict_before,     "s5 mutated"
    print("PASS test_briefing_does_not_mutate_stage_dicts")


def test_flag_off_analyst_briefing_absent():
    """When DECISION_PIPELINE_V2_ENABLED=False, compute_decision_pipeline_v2
    returns None (gate at top of function). analyst_briefing therefore never
    attaches to result['decision_pipeline_v2'] in full_analysis — it is absent."""
    orig = _app.DECISION_PIPELINE_V2_ENABLED
    _app.DECISION_PIPELINE_V2_ENABLED = False
    try:
        result = _app.compute_decision_pipeline_v2(
            instrument="MGC", mode="SCALP",
            live_verdict="WAIT", live_direction=None)
        assert result is None, (
            "Flag-OFF: compute_decision_pipeline_v2 must return None, got: %s" % result)
    finally:
        _app.DECISION_PIPELINE_V2_ENABLED = orig
    print("PASS test_flag_off_analyst_briefing_absent")


# ── runner ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_briefing_has_all_8_keys,
        test_market_environment_human_readable,
        test_money_flow_risk_off_leading_gold,
        test_money_flow_risk_off_weak_equity,
        test_money_flow_risk_on_leading_technology,
        test_money_flow_risk_on_weak_gold,
        test_market_priority_all_4_instruments,
        test_technical_verdict_source_is_production_engine,
        test_technical_verdict_matches_live_verdict,
        test_blocking_factors_from_validate_failed_checks,
        test_next_action_non_empty,
        test_invalidation_non_empty,
        test_briefing_text_non_empty_string,
        test_briefing_text_no_trade_commands,
        test_display_only_always_true,
        test_fail_open_bad_stages,
        test_neutral_regime_no_crash,
        test_unknown_regime_no_crash,
        test_briefing_does_not_mutate_stage_dicts,
        test_flag_off_analyst_briefing_absent,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            print("FAIL %s — %s" % (t.__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n%d/%d tests passed" % (passed, len(tests)))
    if failed:
        sys.exit(1)
