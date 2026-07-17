"""
test_dpv2_phase3.py — DPv2 Analyst Briefing: narrative_summary tests (12 tests)

Tests confirm:
 1. Narrative uses only existing briefing fields
 2. Narrative does not call analysis or trading functions
 3. WAIT output includes blocking reason
 4. READY output does not invent blocking factors
 5. Missing data produces safe fallback language
 6. Summary is 90 words or fewer
 7. All prohibited execution terms are absent
 8. display_only remains True
 9. narrative_summary_source is dpv2_existing_fields
10. Feature flag OFF preserves byte-identical behavior
11. Existing golden tests remain byte-identical
12. No broker, gateway, or order-routing function is called
"""
import os, subprocess, sys, types, unittest

os.environ.setdefault("DECISION_PIPELINE_V2_ENABLED", "1")
import app

PROHIBITED = ["EXECUTE", "ROUTE", "ORDER", "SUBMIT", "FIRE"]

# ── fixture helpers ──────────────────────────────────────────────────────────

def _s1(inst="MGC"):
    return {"status": "OK", "instrument": inst, "price": 3350.0,
            "vwap_value": 3340.0, "price_above_vwap": True,
            "cvd_state": "bullish", "rvol_value": 1.6,
            "vol_spike_fresh": True, "atr_pts": 6.0, "vol_ratio": 1.3,
            "structure_signals": [{"type": "BOS", "direction": "LONG", "age_s": 200.0}],
            "sweep_signals": [], "has_position": False, "active_trade": None,
            "market_open": True, "daily_loss_cap": None,
            "market_env_snapshot": None, "data_coverage": 0.85,
            "price_age_s": 5.0, "vwap_age_s": 20.0, "cvd_age_s": 15.0,
            "vol_spike_age_s": 30.0}

def _s2(regime="RISK_OFF", driver="GEOPOLITICAL"):
    return {"status": "OK", "source": "instrument_derived",
            "regime": regime, "regime_confidence": 75, "primary_driver": driver,
            "secondary_drivers": [], "risk_state": "DEFENSIVE",
            "dominant_theme": "SAFE_HAVEN_DEMAND",
            "supporting_evidence": ["price_above_vwap"],
            "conflicting_evidence": [], "data_quality": "MODERATE"}

def _s3_from(s1, s2, mode="SCALP"):
    return app._dpv2_stage_prioritize(s1, s2, mode)

def _s4(passed=None, failed=None):
    return {"status": "OK", "symbol": "MGC", "strategy": "DPV2",
            "technical_status": "WAIT", "technical_confidence": 60,
            "passed_checks": passed or [],
            "failed_checks": failed or [],
            "risk_checks": {}, "account_checks": {}, "pass_rate": 60}

def _s5(verdict="WAIT", expl=None):
    return {"status": "OK", "symbol": "MGC",
            "production_verdict": verdict, "shadow_verdict": verdict,
            "explanation": expl or {
                "what_must_happen_next": "Wait for a bullish candle to close above VWAP.",
                "what_invalidates_the_idea": "A sustained close below the active demand zone invalidates the long."
            },
            "shadow_mode": True, "pipeline_verdict": verdict,
            "pipeline_direction": None, "pipeline_confidence": 60,
            "pipeline_reasoning": [],
            "agreement_with_live": True, "live_verdict": verdict,
            "live_direction": None, "divergence_reason": None}

def _briefing(s2=None, s4=None, s5=None, inst="MGC", mode="SCALP"):
    s1 = _s1(inst)
    s2_ = s2 or _s2()
    s3_ = _s3_from(s1, s2_, mode)
    s4_ = s4 or _s4()
    s5_ = s5 or _s5()
    return app._dpv2_analyst_briefing(s1, s2_, s3_, s4_, s5_, inst, mode)


class TestNarrativeSummary(unittest.TestCase):

    # ── 1. Narrative uses only existing briefing fields ──────────────────────
    def test_01_narrative_uses_existing_fields_only(self):
        """narrative_summary is assembled from regime/driver/flow/top/verdict/blocking/action/inv."""
        b = _briefing()
        ns = b.get("narrative_summary", "")
        self.assertTrue(b.get("available"))
        self.assertTrue(len(ns) > 0, "narrative_summary must not be empty")
        # Regime label appears in narrative
        self.assertIn("Risk Off", ns, "Regime label must appear in narrative")
        # Top market appears
        self.assertIn("MGC", ns, "Top market symbol must appear in narrative")

    # ── 2. Narrative does not call analysis/trading functions ────────────────
    def test_02_no_analysis_or_trading_functions_called(self):
        """_dpv2_analyst_briefing runs without touching full_analysis or gate functions."""
        calls = []
        sentinel = types.SimpleNamespace

        _orig_fa = getattr(app, "full_analysis", None)
        _orig_es = getattr(app, "evaluate_strict_setup", None)

        def _stub_fa(*a, **kw): calls.append("full_analysis"); return {}
        def _stub_es(*a, **kw): calls.append("evaluate_strict_setup"); return {}

        try:
            if _orig_fa:
                app.full_analysis = _stub_fa
            if _orig_es:
                app.evaluate_strict_setup = _stub_es
            b = _briefing()
            self.assertNotIn("full_analysis", calls)
            self.assertNotIn("evaluate_strict_setup", calls)
            self.assertTrue(b.get("available"))
        finally:
            if _orig_fa:
                app.full_analysis = _orig_fa
            if _orig_es:
                app.evaluate_strict_setup = _orig_es

    # ── 3. WAIT output includes blocking reason ──────────────────────────────
    def test_03_wait_includes_blocking_reason(self):
        """WAIT narrative mentions blocking factor when specific failed checks present."""
        b = _briefing(
            s4=_s4(passed=["Fresh BOS 200s ago"],
                   failed=["VWAP confirmation window too wide"]),
            s5=_s5("WAIT")
        )
        ns = b.get("narrative_summary", "")
        self.assertIn("WAIT", ns, "Narrative must mention WAIT verdict")
        self.assertIn("vwap confirmation window too wide", ns.lower(),
                      "Narrative must include the specific blocking reason")

    # ── 4. READY output does not invent blocking factors ────────────────────
    def test_04_ready_no_invented_blocking_factors(self):
        """READY narrative contains no blocking-factor language."""
        b = _briefing(
            s4=_s4(passed=["Fresh BOS", "Price above VWAP", "Bullish CVD"], failed=[]),
            s5=_s5("LONG READY", expl={
                "what_must_happen_next": "Enter on the next 5m open.",
                "what_invalidates_the_idea": "A close below the demand zone invalidates the long."
            })
        )
        ns = b.get("narrative_summary", "")
        self.assertIn("LONG READY", ns, "Narrative must mention LONG READY verdict")
        self.assertNotIn("because", ns.lower(),
                         "READY narrative must not contain 'because' (no blocking factors)")
        self.assertNotIn("pending", ns.lower(),
                         "READY narrative must not mention pending confirmation")

    # ── 5. Missing data produces safe fallback language ──────────────────────
    def test_05_missing_data_safe_fallbacks(self):
        """None/empty stage inputs produce safe fallback text, no None/null visible."""
        b = app._dpv2_analyst_briefing(
            {"status": "OK"},
            {"status": "OK", "regime": None, "primary_driver": None},
            {"status": "OK", "ranked_instruments": None},
            {"status": "OK", "passed_checks": None, "failed_checks": None},
            {"status": "OK", "explanation": None, "production_verdict": None},
            "MGC", "SCALP"
        )
        ns = b.get("narrative_summary", "")
        self.assertTrue(len(ns) > 0, "Fallback narrative must not be empty")
        self.assertNotIn("None", ns,  "Must not contain literal 'None'")
        self.assertNotIn("null", ns,  "Must not contain literal 'null'")
        self.assertIn("undetermined", ns.lower(),
                      "Fallback must say market conditions are undetermined")

    # ── 6. Summary is 90 words or fewer ─────────────────────────────────────
    def test_06_word_count_at_most_90(self):
        """narrative_summary must not exceed 90 words in any case."""
        cases = [
            _briefing(),
            _briefing(
                s4=_s4(failed=["VWAP confirmation window too wide",
                                "No confirmation candle close",
                                "Volume insufficient"]),
                s5=_s5("WAIT")
            ),
            app._dpv2_analyst_briefing(
                {"status": "OK"},
                {"status": "OK", "regime": None, "primary_driver": None},
                {"status": "OK", "ranked_instruments": None},
                {"status": "OK", "passed_checks": None, "failed_checks": None},
                {"status": "OK", "explanation": None, "production_verdict": None},
                "MGC", "SCALP"
            ),
        ]
        for b in cases:
            ns = b.get("narrative_summary", "")
            wc = len(ns.split())
            self.assertLessEqual(wc, 90, "narrative_summary exceeds 90 words: %d words in: %r" % (wc, ns))

    # ── 7. No prohibited execution terms ────────────────────────────────────
    def test_07_no_prohibited_execution_terms(self):
        """EXECUTE, ROUTE, ORDER, SUBMIT, FIRE must not appear anywhere in the briefing."""
        b = _briefing()
        ns = b.get("narrative_summary", "").upper()
        bt = b.get("briefing_text", "").upper()
        for term in PROHIBITED:
            self.assertNotIn(term, ns,  "Prohibited term '%s' found in narrative_summary" % term)
            self.assertNotIn(term, bt,  "Prohibited term '%s' found in briefing_text" % term)

    # ── 8. display_only remains True ────────────────────────────────────────
    def test_08_display_only_true(self):
        """display_only must be True."""
        b = _briefing()
        self.assertTrue(b.get("display_only") is True, "display_only must be True")

    # ── 9. narrative_summary_source is dpv2_existing_fields ─────────────────
    def test_09_narrative_summary_source(self):
        """narrative_summary_source must be 'dpv2_existing_fields'."""
        b = _briefing()
        self.assertEqual(
            b.get("narrative_summary_source"),
            "dpv2_existing_fields",
            "narrative_summary_source must be 'dpv2_existing_fields'"
        )

    # ── 10. Feature flag OFF preserves byte-identical behavior ───────────────
    def test_10_flag_off_no_narrative_key(self):
        """When DPv2 flag is OFF, compute_decision_pipeline_v2 returns None (key never attached)."""
        orig = app.DECISION_PIPELINE_V2_ENABLED
        try:
            app.DECISION_PIPELINE_V2_ENABLED = False
            result = app.compute_decision_pipeline_v2("MGC", "SCALP", "WAIT", None)
            # flag-OFF early return is None — key is never attached to full_analysis result
            self.assertIsNone(result,
                              "Flag-OFF must return None (not a dict); got: %r" % result)
        finally:
            app.DECISION_PIPELINE_V2_ENABLED = orig

    # ── 11. Existing golden tests remain byte-identical ──────────────────────
    def test_11_golden_parity(self):
        """All 4 existing golden checks must still pass (byte-identical)."""
        import pathlib
        workspace = str(pathlib.Path(__file__).resolve().parent.parent.parent)
        goldens = [
            ".local/state/check_parity.sh",
            ".local/state/check_scalp_golden.sh",
            ".local/state/check_dual_sim.sh",
            ".local/state/check_breakout_mode.sh",
        ]
        for script in goldens:
            r = subprocess.run(["bash", script], capture_output=True, text=True,
                               timeout=120, cwd=workspace)
            self.assertEqual(r.returncode, 0,
                             "Golden %s FAILED:\nstdout: %s\nstderr: %s" % (script, r.stdout, r.stderr))

    # ── 12. No broker/gateway/order-routing function called ──────────────────
    def test_12_no_broker_or_gateway_calls(self):
        """_dpv2_analyst_briefing must not call any gateway/broker/TradersPost function."""
        gateway_fns = [
            "execute_trade_gateway",
            "_send_to_traderspost",
            "_send_discord",
            "_enqueue_slow",
            "send_to_traderspost",
        ]
        called = []
        originals = {}
        for fn_name in gateway_fns:
            orig = getattr(app, fn_name, None)
            if orig:
                originals[fn_name] = orig
                def _make_stub(name):
                    def _stub(*a, **kw):
                        called.append(name)
                        return None
                    return _stub
                setattr(app, fn_name, _make_stub(fn_name))
        try:
            b = _briefing()
            self.assertTrue(b.get("available"))
            self.assertEqual(called, [],
                             "Gateway/broker functions called unexpectedly: %s" % called)
        finally:
            for fn_name, orig in originals.items():
                setattr(app, fn_name, orig)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite  = loader.loadTestsFromTestCase(TestNarrativeSummary)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total  = result.testsRun
    passed = total - len(result.failures) - len(result.errors)
    print("\n%d/%d tests passed" % (passed, total))
    sys.exit(0 if result.wasSuccessful() else 1)
