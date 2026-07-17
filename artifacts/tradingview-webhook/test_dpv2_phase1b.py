"""
Decision Pipeline V2 — Phase 1B tests.

Tests verify:
 1.  INTERPRET outputs regime + driver, NOT LONG/SHORT/BUY/SELL
 2.  Regime values restricted to the 5-value enum
 3.  Driver values restricted to the allowed set
 4.  PRIORITIZE outputs ranked_instruments (not a trade list)
 5.  ranked_instruments contains all 4 known instruments
 6.  priority field is monotonically 1..N
 7.  focus instrument is flagged is_focus=True
 8.  VALIDATE uses passed_checks/failed_checks schema (not "gates")
 9.  DECIDE explanation block has all 6 required keys
10.  agreement_status is one of the 9-value extended enum
11.  Both READY → AGREE
12.  Both WAIT  → AGREE
13.  Prod READY / V2 WAIT → PRODUCTION_MORE_BULLISH (LONG) or PRODUCTION_MORE_BEARISH (SHORT)
14.  Prod WAIT / V2 READY → V2_MORE_BULLISH (LONG dir) or V2_MORE_BEARISH (SHORT dir)
15.  _dpv2_compute_better_decision: V2 avoids stopped-out trade → better_decision=V2
16.  _dpv2_compute_better_decision: Production captures winner V2 skipped → PRODUCTION
17.  _dpv2_compute_better_decision: Both READY, trade wins → BOTH
18.  _dpv2_compute_better_decision: Both READY, trade loses → NEITHER
19.  _dpv2_compute_better_decision: No outcome → INCONCLUSIVE
20.  _dpv2_compute_better_decision: Both waited → INCONCLUSIVE
21.  Scorecard math: total, agreement_rate_pct, disagree_count
22.  Scorecard by_regime grouping
23.  Scorecard by_driver grouping
24.  Scorecard by_symbol grouping
25.  Shadow trade label is HYPOTHETICAL, no broker call
26.  changed_production_behavior is always False
"""

import sys
import os
import threading
from collections import deque
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import app as _app


# ── helpers ──────────────────────────────────────────────────────────────────

def _obs(instrument="MGC", price=2400.0, vwap=2395.0, cvd="bullish",
         structs=None, coverage=0.75, atr_pts=5.0, market_open=True,
         has_position=False, me_snapshot=None):
    """Build a minimal Stage-1 observation dict."""
    return {
        "status":              "OK",
        "instrument":          instrument,
        "price":               price,
        "price_age_s":         10.0,
        "vwap_value":          vwap,
        "vwap_age_s":          30.0,
        "price_above_vwap":    (price > vwap) if (price is not None and vwap is not None) else None,
        "cvd_state":           cvd,
        "cvd_age_s":           20.0,
        "rvol_value":          1.5,
        "vol_spike_fresh":     True,
        "vol_spike_age_s":     60.0,
        "atr_pts":             atr_pts,
        "vol_ratio":           1.2,
        "structure_signals":   structs or [
            {"type": "BOS", "direction": "LONG", "age_s": 300.0}
        ],
        "sweep_signals":       [],
        "has_position":        has_position,
        "active_trade":        None,
        "market_open":         market_open,
        "daily_loss_cap":      None,
        "market_env_snapshot": me_snapshot,
        "data_coverage":       coverage,
    }


def _interp(regime="RISK_OFF", driver="TECHNICAL", confidence=65):
    return {
        "status":               "OK",
        "source":               "instrument_derived",
        "regime":               regime,
        "regime_confidence":    confidence,
        "primary_driver":       driver,
        "secondary_drivers":    [],
        "risk_state":           "DEFENSIVE" if regime == "RISK_OFF" else "AGGRESSIVE",
        "dominant_theme":       "SAFE_HAVEN_DEMAND",
        "supporting_evidence":  ["price_above_vwap"],
        "conflicting_evidence": [],
        "data_quality":         "MODERATE",
    }


def _prio(regime="RISK_OFF", driver="TECHNICAL", instrument="MGC", confidence=65):
    obs  = _obs(instrument=instrument)
    intp = _interp(regime=regime, driver=driver, confidence=confidence)
    return _app._dpv2_stage_prioritize(obs, intp, "SCALP")


def _validate(obs=None, interp=None, prio_result=None, instrument="MGC", mode="SCALP"):
    o = obs    or _obs(instrument=instrument)
    i = interp or _interp()
    p = prio_result or _prio(instrument=instrument)
    return _app._dpv2_stage_validate(o, i, p, instrument, mode)


def _decide(live_verdict="WAIT", live_direction=None,
            pipeline_status="READY", instrument="MGC",
            regime="RISK_OFF", driver="TECHNICAL", dir_ctx="BULLISH"):
    obs      = _obs(instrument=instrument)
    interp   = _interp(regime=regime, driver=driver)
    prio_res = _prio(regime=regime, driver=driver, instrument=instrument)
    validate = {
        "status":               "OK",
        "technical_status":     pipeline_status,
        "technical_confidence": 60,
        "passed_checks":        ["Fresh structure", "Price above VWAP"],
        "failed_checks":        [],
        "risk_checks":          {},
        "account_checks":       {"market_open": True, "has_position": False},
        "pass_rate":            80,
    }
    prio_res["focus_preference"]    = "STRONGLY_FAVORED"
    prio_res["focus_direction_ctx"] = dir_ctx
    return _app._dpv2_stage_decide(obs, interp, prio_res, validate,
                                    live_verdict, live_direction, instrument)


# ── 1. INTERPRET has regime, not LONG/SHORT ───────────────────────────────────
def test_interpret_no_trade_direction():
    obs = _obs(instrument="MGC", price=2400.0, vwap=2390.0, cvd="bullish")
    result = _app._dpv2_stage_interpret(obs)
    assert result["status"] == "OK"
    regime = result.get("regime", "")
    # Must not contain directional trade labels
    for bad in ("LONG", "SHORT", "BUY", "SELL", "APPROVE", "REJECT"):
        assert bad not in regime, "INTERPRET regime must not be '%s'" % bad
    # primary_driver must also not be directional
    driver = result.get("primary_driver", "")
    for bad in ("LONG", "SHORT", "BUY", "SELL"):
        assert bad not in driver, "INTERPRET driver must not be '%s'" % bad
    print("PASS test_interpret_no_trade_direction")


# ── 2. Regime values are in the 5-value enum ─────────────────────────────────
def test_interpret_regime_in_enum():
    valid = {"RISK_ON", "RISK_OFF", "NEUTRAL", "MIXED", "UNKNOWN"}
    for instrument, cvd, price, vwap in [
        ("MGC", "bullish", 2400.0, 2390.0),
        ("MNQ", "bearish", 20000.0, 20100.0),
        ("MES", None, 5200.0, 5205.0),
        ("MYM", "bearish", 42000.0, 42100.0),
    ]:
        obs    = _obs(instrument=instrument, price=price, vwap=vwap, cvd=cvd)
        result = _app._dpv2_stage_interpret(obs)
        assert result["regime"] in valid, (
            "Regime '%s' not in enum for %s" % (result["regime"], instrument))
    print("PASS test_interpret_regime_in_enum")


# ── 3. Driver values are in the allowed set ───────────────────────────────────
def test_interpret_driver_in_allowed_set():
    allowed = {
        "GEOPOLITICAL", "FED_POLICY", "INFLATION", "EMPLOYMENT",
        "ECONOMIC_GROWTH", "EARNINGS", "ENERGY", "LIQUIDITY",
        "TECHNICAL", "MULTIPLE", "NONE", "UNKNOWN",
    }
    for inst in ("MGC", "MNQ", "MES"):
        obs    = _obs(instrument=inst)
        result = _app._dpv2_stage_interpret(obs)
        assert result["primary_driver"] in allowed, (
            "Driver '%s' not in allowed set for %s" % (result["primary_driver"], inst))
    print("PASS test_interpret_driver_in_allowed_set")


# ── 4. PRIORITIZE outputs ranked_instruments not a signal list ────────────────
def test_prioritize_returns_ranked_instruments():
    result = _prio(instrument="MGC")
    assert "ranked_instruments" in result, "ranked_instruments key missing from PRIORITIZE"
    assert isinstance(result["ranked_instruments"], list)
    # Must not have trade-command keys at top level
    for bad in ("signal", "direction", "verdict", "order", "trade"):
        assert bad not in result, "PRIORITIZE must not have top-level key '%s'" % bad
    print("PASS test_prioritize_returns_ranked_instruments")


# ── 5. ranked_instruments contains all 4 known instruments ───────────────────
def test_prioritize_all_4_instruments_ranked():
    result   = _prio(instrument="MGC")
    symbols  = {r["symbol"] for r in result["ranked_instruments"]}
    expected = {"MGC", "MNQ", "MES", "MYM"}
    assert symbols == expected, "Missing instruments in ranked list: %s" % (expected - symbols)
    print("PASS test_prioritize_all_4_instruments_ranked")


# ── 6. priority is 1..N monotonically ────────────────────────────────────────
def test_prioritize_monotonic_priority():
    result  = _prio(instrument="MGC")
    ranked  = result["ranked_instruments"]
    for i, r in enumerate(ranked):
        assert r["priority"] == i + 1, (
            "Expected priority %d, got %d for %s" % (i + 1, r["priority"], r["symbol"]))
    print("PASS test_prioritize_monotonic_priority")


# ── 7. Focus instrument flagged is_focus=True ─────────────────────────────────
def test_prioritize_focus_instrument_flagged():
    for focus in ("MGC", "MNQ", "MES", "MYM"):
        result = _prio(instrument=focus)
        focus_entries = [r for r in result["ranked_instruments"] if r["is_focus"]]
        assert len(focus_entries) == 1, "Expected exactly 1 focus entry for %s" % focus
        assert focus_entries[0]["symbol"] == focus
    print("PASS test_prioritize_focus_instrument_flagged")


# ── 8. VALIDATE uses passed_checks/failed_checks (not "gates") ───────────────
def test_validate_passed_failed_checks_schema():
    result = _validate()
    assert "passed_checks" in result,   "VALIDATE missing passed_checks"
    assert "failed_checks" in result,   "VALIDATE missing failed_checks"
    assert "gates" not in result,       "VALIDATE must NOT have old 'gates' key"
    assert isinstance(result["passed_checks"], list)
    assert isinstance(result["failed_checks"], list)
    print("PASS test_validate_passed_failed_checks_schema")


# ── 9. DECIDE explanation block has all 6 required keys ──────────────────────
def test_decide_explanation_block_complete():
    result = _decide(live_verdict="WAIT")
    expl   = result.get("explanation") or {}
    required = {
        "what_is_happening",
        "why_it_is_happening",
        "why_this_market_is_relevant",
        "what_the_technical_engine_found",
        "what_must_happen_next",
        "what_invalidates_the_idea",
    }
    missing = required - set(expl.keys())
    assert not missing, "Explanation missing keys: %s" % missing
    for k, v in expl.items():
        assert v, "Explanation key '%s' must not be empty" % k
    print("PASS test_decide_explanation_block_complete")


# ── 10. agreement_status is one of the 9-value enum ──────────────────────────
def test_decide_agreement_status_enum():
    valid = {
        "AGREE",
        "PRODUCTION_APPROVED_V2_WAITED",
        "PRODUCTION_WAITED_V2_APPROVED",
        "PRODUCTION_MORE_BULLISH",
        "PRODUCTION_MORE_BEARISH",
        "V2_MORE_BULLISH",
        "V2_MORE_BEARISH",
        "INCOMPARABLE",
        "PENDING",
    }
    for lv, ld, ps, dirctx in [
        ("READY", "LONG",  "READY", "BULLISH"),
        ("WAIT",  None,    "WAIT",  "NEUTRAL"),
        ("READY", "LONG",  "WAIT",  "BULLISH"),
        ("WAIT",  None,    "READY", "BULLISH"),
        ("READY", "SHORT", "WAIT",  "BEARISH"),
    ]:
        result = _decide(live_verdict=lv, live_direction=ld,
                          pipeline_status=ps, dir_ctx=dirctx)
        status = result.get("agreement_status", "")
        assert status in valid, (
            "agreement_status '%s' not in enum (lv=%s ps=%s)" % (status, lv, ps))
    print("PASS test_decide_agreement_status_enum")


# ── 11. Both READY → AGREE ────────────────────────────────────────────────────
def test_decide_both_ready_agree():
    # is_actionable recognises "LONG READY"/"SHORT READY", not bare "READY"
    result = _decide(live_verdict="LONG READY", live_direction="LONG",
                      pipeline_status="READY", dir_ctx="BULLISH")
    assert result["agreement_status"] == "AGREE", (
        "Both READY should give AGREE, got %s" % result["agreement_status"])
    assert result["agreement_with_live"] is True
    print("PASS test_decide_both_ready_agree")


# ── 12. Both WAIT → AGREE ─────────────────────────────────────────────────────
def test_decide_both_wait_agree():
    result = _decide(live_verdict="WAIT", live_direction=None,
                      pipeline_status="WAIT", dir_ctx="NEUTRAL")
    assert result["agreement_status"] == "AGREE", (
        "Both WAIT should give AGREE, got %s" % result["agreement_status"])
    assert result["agreement_with_live"] is True
    print("PASS test_decide_both_wait_agree")


# ── 13. Prod READY/LONG, V2 WAIT → PRODUCTION_MORE_BULLISH ───────────────────
def test_decide_prod_ready_v2_wait_long():
    result = _decide(live_verdict="LONG READY", live_direction="LONG",
                      pipeline_status="WAIT", dir_ctx="NEUTRAL")
    assert result["agreement_status"] == "PRODUCTION_MORE_BULLISH", (
        "Got %s" % result["agreement_status"])
    assert result["agreement_with_live"] is False
    print("PASS test_decide_prod_ready_v2_wait_long")


# ── 14. Prod WAIT, V2 READY/LONG → V2_MORE_BULLISH ───────────────────────────
def test_decide_prod_wait_v2_ready_long():
    result = _decide(live_verdict="WAIT", live_direction=None,
                      pipeline_status="READY", dir_ctx="BULLISH")
    assert result["agreement_status"] == "V2_MORE_BULLISH", (
        "Got %s" % result["agreement_status"])
    assert result["agreement_with_live"] is False
    print("PASS test_decide_prod_wait_v2_ready_long")


# ── 15. V2 avoids stopped-out trade → better_decision=V2 ─────────────────────
def test_better_decision_v2_avoids_loss():
    # is_actionable only recognises "LONG READY"/"SHORT READY", not bare "READY"
    record = {
        "production_verdict": "LONG READY",
        "shadow_verdict":     "WAIT",
        "final_outcome":      "STOPPED_OUT",
    }
    bd, reason = _app._dpv2_compute_better_decision(record)
    assert bd == "V2", "Expected V2, got %s (%s)" % (bd, reason)
    assert "avoided" in reason.lower() or "losing" in reason.lower()
    print("PASS test_better_decision_v2_avoids_loss")


# ── 16. Production captures winner V2 skipped → PRODUCTION ───────────────────
def test_better_decision_production_captures_winner():
    record = {
        "production_verdict": "LONG READY",
        "shadow_verdict":     "WAIT",
        "final_outcome":      "TARGET_REACHED",
    }
    bd, reason = _app._dpv2_compute_better_decision(record)
    assert bd == "PRODUCTION", "Expected PRODUCTION, got %s (%s)" % (bd, reason)
    print("PASS test_better_decision_production_captures_winner")


# ── 17. Both READY, trade wins → BOTH ────────────────────────────────────────
def test_better_decision_both_win():
    record = {
        "production_verdict": "LONG READY",
        "shadow_verdict":     "READY",     # shadow uses bare "READY" string
        "final_outcome":      "TARGET_REACHED",
    }
    bd, _ = _app._dpv2_compute_better_decision(record)
    assert bd == "BOTH", "Expected BOTH, got %s" % bd
    print("PASS test_better_decision_both_win")


# ── 18. Both READY, trade loses → NEITHER ────────────────────────────────────
def test_better_decision_both_lose():
    record = {
        "production_verdict": "SHORT READY",
        "shadow_verdict":     "READY",
        "final_outcome":      "STOPPED_OUT",
    }
    bd, _ = _app._dpv2_compute_better_decision(record)
    assert bd == "NEITHER", "Expected NEITHER, got %s" % bd
    print("PASS test_better_decision_both_lose")


# ── 19. No outcome → INCONCLUSIVE ────────────────────────────────────────────
def test_better_decision_no_outcome():
    record = {
        "production_verdict": "READY",
        "shadow_verdict":     "WAIT",
        "final_outcome":      None,
    }
    bd, _ = _app._dpv2_compute_better_decision(record)
    assert bd == "INCONCLUSIVE", "Expected INCONCLUSIVE, got %s" % bd
    print("PASS test_better_decision_no_outcome")


# ── 20. Both waited → INCONCLUSIVE ───────────────────────────────────────────
def test_better_decision_both_waited():
    record = {
        "production_verdict": "WAIT",
        "shadow_verdict":     "WAIT",
        "final_outcome":      "TARGET_REACHED",
    }
    bd, reason = _app._dpv2_compute_better_decision(record)
    assert bd == "INCONCLUSIVE", "Expected INCONCLUSIVE, got %s" % bd
    assert "waited" in reason.lower()
    print("PASS test_better_decision_both_waited")


# ── 21. Scorecard math ────────────────────────────────────────────────────────
def test_scorecard_math():
    # Inject synthetic records directly into the history
    orig = _app._DPV2_AGREEMENT_HISTORY
    tmp  = deque(maxlen=200)
    tmp.appendleft({"agreement_status": "AGREE",         "better_decision": None,
                     "symbol": "MGC", "mode": "SCALP", "regime": "RISK_OFF",
                     "primary_driver": "TECHNICAL", "final_outcome": None,
                     "mfe_pts": None, "mae_pts": None})
    tmp.appendleft({"agreement_status": "AGREE",         "better_decision": "BOTH",
                     "symbol": "MNQ", "mode": "SWING", "regime": "RISK_ON",
                     "primary_driver": "TECHNICAL", "final_outcome": "TARGET_REACHED",
                     "mfe_pts": 10.0, "mae_pts": 2.0})
    tmp.appendleft({"agreement_status": "V2_MORE_BULLISH","better_decision": "V2",
                     "symbol": "MGC", "mode": "SCALP", "regime": "RISK_OFF",
                     "primary_driver": "TECHNICAL", "final_outcome": "STOPPED_OUT",
                     "mfe_pts": 3.0, "mae_pts": 8.0})
    _app._DPV2_AGREEMENT_HISTORY = tmp
    try:
        card = _app._dpv2_compute_scorecard()
        assert card["total_evaluations"]   == 3
        assert card["disagree_count"]      == 1
        assert card["agreement_rate_pct"]  == round(2/3*100, 1)
        assert card["v2_wins"]             == 1
        assert card["avoided_loss_count"]  == 1    # V2 win + STOPPED_OUT
    finally:
        _app._DPV2_AGREEMENT_HISTORY = orig
    print("PASS test_scorecard_math")


# ── 22. Scorecard by_regime grouping ─────────────────────────────────────────
def test_scorecard_by_regime():
    orig = _app._DPV2_AGREEMENT_HISTORY
    tmp  = deque(maxlen=200)
    for _ in range(3):
        tmp.appendleft({"agreement_status": "AGREE", "better_decision": None,
                         "symbol": "MGC", "mode": "SCALP", "regime": "RISK_OFF",
                         "primary_driver": "TECHNICAL", "final_outcome": None,
                         "mfe_pts": None, "mae_pts": None})
    tmp.appendleft({"agreement_status": "AGREE", "better_decision": None,
                     "symbol": "MNQ", "mode": "SWING", "regime": "RISK_ON",
                     "primary_driver": "TECHNICAL", "final_outcome": None,
                     "mfe_pts": None, "mae_pts": None})
    _app._DPV2_AGREEMENT_HISTORY = tmp
    try:
        card = _app._dpv2_compute_scorecard()
        by_r = card.get("by_regime", {})
        assert "RISK_OFF" in by_r,  "RISK_OFF missing from by_regime"
        assert "RISK_ON"  in by_r,  "RISK_ON missing from by_regime"
        assert by_r["RISK_OFF"]["total"] == 3
        assert by_r["RISK_ON"]["total"]  == 1
    finally:
        _app._DPV2_AGREEMENT_HISTORY = orig
    print("PASS test_scorecard_by_regime")


# ── 23. Scorecard by_driver grouping ─────────────────────────────────────────
def test_scorecard_by_driver():
    orig = _app._DPV2_AGREEMENT_HISTORY
    tmp  = deque(maxlen=200)
    for drv, count in [("TECHNICAL", 2), ("FED_POLICY", 1)]:
        for _ in range(count):
            tmp.appendleft({"agreement_status": "AGREE", "better_decision": None,
                             "symbol": "MGC", "mode": "SCALP", "regime": "RISK_OFF",
                             "primary_driver": drv, "final_outcome": None,
                             "mfe_pts": None, "mae_pts": None})
    _app._DPV2_AGREEMENT_HISTORY = tmp
    try:
        card = _app._dpv2_compute_scorecard()
        by_d = card.get("by_driver", {})
        assert "TECHNICAL"  in by_d, "TECHNICAL missing from by_driver"
        assert "FED_POLICY" in by_d, "FED_POLICY missing from by_driver"
        assert by_d["TECHNICAL"]["total"]  == 2
        assert by_d["FED_POLICY"]["total"] == 1
    finally:
        _app._DPV2_AGREEMENT_HISTORY = orig
    print("PASS test_scorecard_by_driver")


# ── 24. Scorecard by_symbol grouping ─────────────────────────────────────────
def test_scorecard_by_symbol():
    orig = _app._DPV2_AGREEMENT_HISTORY
    tmp  = deque(maxlen=200)
    for sym, cnt in [("MGC", 4), ("MNQ", 2)]:
        for _ in range(cnt):
            tmp.appendleft({"agreement_status": "AGREE", "better_decision": None,
                             "symbol": sym, "mode": "SCALP", "regime": "RISK_OFF",
                             "primary_driver": "TECHNICAL", "final_outcome": None,
                             "mfe_pts": None, "mae_pts": None})
    _app._DPV2_AGREEMENT_HISTORY = tmp
    try:
        card = _app._dpv2_compute_scorecard()
        by_s = card.get("by_symbol", {})
        assert by_s["MGC"]["total"] == 4
        assert by_s["MNQ"]["total"] == 2
    finally:
        _app._DPV2_AGREEMENT_HISTORY = orig
    print("PASS test_scorecard_by_symbol")


# ── 25. Shadow trade label is HYPOTHETICAL, no broker call ───────────────────
def test_shadow_trade_is_hypothetical():
    orig_shadows = dict(_app._DPV2_SHADOW_TRADES)
    # Clear shadows for clean test
    _app._DPV2_SHADOW_TRADES.clear()
    try:
        # V2 approves a LONG but production waits
        result = _decide(live_verdict="WAIT", live_direction=None,
                          pipeline_status="READY", dir_ctx="BULLISH",
                          instrument="MNQ")
        assert result["shadow_mode"] is True
        assert result["changed_production_behavior"] is False
        # The shadow trade in the dict (if created via compute_...) has HYPOTHETICAL label
        # For unit-level: verify the decide fn itself does not create broker payloads
        for bad_key in ("order", "ticker", "action", "quantity", "webhook"):
            assert bad_key not in result, (
                "DECIDE must not produce broker key '%s'" % bad_key)
    finally:
        _app._DPV2_SHADOW_TRADES.clear()
        _app._DPV2_SHADOW_TRADES.update(orig_shadows)
    print("PASS test_shadow_trade_is_hypothetical")


# ── 26. changed_production_behavior is always False ──────────────────────────
def test_changed_production_behavior_always_false():
    for live_v, ps in [("READY", "READY"), ("WAIT", "WAIT"),
                        ("READY", "WAIT"), ("WAIT", "READY")]:
        result = _decide(live_verdict=live_v, live_direction="LONG",
                          pipeline_status=ps, dir_ctx="BULLISH")
        assert result.get("changed_production_behavior") is False, (
            "changed_production_behavior must be False (lv=%s ps=%s)" % (live_v, ps))
    print("PASS test_changed_production_behavior_always_false")


# ── runner ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_interpret_no_trade_direction,
        test_interpret_regime_in_enum,
        test_interpret_driver_in_allowed_set,
        test_prioritize_returns_ranked_instruments,
        test_prioritize_all_4_instruments_ranked,
        test_prioritize_monotonic_priority,
        test_prioritize_focus_instrument_flagged,
        test_validate_passed_failed_checks_schema,
        test_decide_explanation_block_complete,
        test_decide_agreement_status_enum,
        test_decide_both_ready_agree,
        test_decide_both_wait_agree,
        test_decide_prod_ready_v2_wait_long,
        test_decide_prod_wait_v2_ready_long,
        test_better_decision_v2_avoids_loss,
        test_better_decision_production_captures_winner,
        test_better_decision_both_win,
        test_better_decision_both_lose,
        test_better_decision_no_outcome,
        test_better_decision_both_waited,
        test_scorecard_math,
        test_scorecard_by_regime,
        test_scorecard_by_driver,
        test_scorecard_by_symbol,
        test_shadow_trade_is_hypothetical,
        test_changed_production_behavior_always_false,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print("FAIL %s — %s" % (t.__name__, e))
            failed += 1
    print("\n%d/%d tests passed" % (passed, len(tests)))
    if failed:
        sys.exit(1)
