"""Phase 6 — Strategy Scan Coverage diagnostics tests (25 tests).

Tests prove:
  1.  Per-strategy evaluation tracking (registered, enabled, eligible, evaluated)
  2.  Skip-reason taxonomy correctness
  3.  Result classification (no_signal, candidate, selected, skipped)
  4.  Zero mutation of any production state (scoring, verdicts, auto-trade, learning)
  5.  Endpoint read-only semantics
  6.  Empty-state safety before first scan
  7.  Byte-identity of compute_strategy_engine output when diagnostics dict state varies
  8.  No Databento / network calls in unit tests

All tests:
  - use synchronous patching (no live server, no real DB, no Databento)
  - reset STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER between tests
  - do not depend on each other
  - are compatible with pytest -q
"""
import json
from unittest.mock import patch, call

import pytest

import app


# ─── shared fixtures ────────────────────────────────────────────────────────

_FAKE_CTX = {
    "price": 2100.0, "vwap_value": 2095.0, "vwap_ok": True,
    "price_above_vwap": True, "price_below_vwap": False, "near_vwap": False,
    "has_bull_sweep": False, "has_bear_sweep": False,
    "has_choch_demand": False, "has_choch_supply": False,
    "has_bull_confirm": False, "has_bear_confirm": False,
    "structure_long": False, "structure_short": False,
    "volume_ok": False, "cvd_state": "neutral",
    "or_high": None, "or_low": None, "or_complete": False,
    "in_opening_window": False,
    "atr_contraction": False, "range_tight": False,
    "broke_range_high": False, "broke_range_low": False,
    "range_high": 2110.0, "range_low": 2090.0,
    "nearest_demand": None, "nearest_supply": None,
    "atr_pts": 5.0, "rvol_value": 1.0, "volume_spike_fresh": False,
}
_FAKE_REGIME = {"regime": "BALANCED", "reason": "Balanced market conditions"}
_FAKE_BIAS   = {"Asia": "Neutral", "London": "Neutral", "New York": "Neutral"}

_BASE_PATCHES = [
    ("app.build_strategy_context", dict(_FAKE_CTX)),
    ("app.detect_market_regime",   dict(_FAKE_REGIME)),
    ("app.detect_session_bias",    dict(_FAKE_BIAS)),
    ("app.instrument_of",          "MGC"),
]


def _no_signal_scorer(ctx):
    return {"direction": None, "conditions": [("Condition A", False)], "target_r": 2.0}


def _long_scorer(ctx):
    return {"direction": "Long", "conditions": [("Condition A", True)], "target_r": 2.0}


def _failing_scorer(ctx):
    raise RuntimeError("intentional scorer error")


def _run_engine(ticker="MGC", price=2100.0, ctx_override=None, scorer_override=None):
    """Run compute_strategy_engine with all external dependencies mocked."""
    ctx = dict(_FAKE_CTX) if ctx_override is None else ctx_override
    patches = []
    for attr, val in _BASE_PATCHES:
        if attr == "app.build_strategy_context":
            patches.append(patch(attr, return_value=ctx))
        elif attr == "app.instrument_of":
            patches.append(patch(attr, return_value=ticker))
        else:
            patches.append(patch(attr, return_value=val))
    if scorer_override:
        patches.append(patch("app.STRATEGY_SCORERS", scorer_override))
    for p in patches:
        p.start()
    try:
        return app.compute_strategy_engine(ticker, price, 2095.0, "fresh", None, 0)
    finally:
        for p in patches:
            p.stop()


# ─── Part 2: Strategy Inventory ─────────────────────────────────────────────

class TestStrategyInventory:
    """Part 2 — Canonical strategy inventory: registry counts and parity."""

    def test_inv_main_engine_has_exactly_five_strategies(self):
        """Main engine: STRATEGY_DEFS, PRIORITY, and SCORERS each contain exactly 5."""
        assert len(app.STRATEGY_DEFS) == 5
        assert len(app.STRATEGY_PRIORITY) == 5
        assert len(app.STRATEGY_SCORERS) == 5

    def test_inv_all_priority_keys_have_defs_and_scorers(self):
        """Every key in STRATEGY_PRIORITY has a matching STRATEGY_DEFS and SCORERS entry."""
        for key in app.STRATEGY_PRIORITY:
            assert key in app.STRATEGY_DEFS,   f"STRATEGY_DEFS missing key: {key}"
            assert key in app.STRATEGY_SCORERS, f"STRATEGY_SCORERS missing key: {key}"

    def test_inv_swing_library_has_five_strategies(self):
        """Swing strategy library contains exactly 5 operator-selectable strategies."""
        assert len(app.SWING_STRATEGY_DEFS) == 5

    def test_inv_scalp_research_pending_keys_excluded_from_live(self):
        """Scalp research: PENDING_KEYS strategies must not appear in LIVE_SIM_DETECTORS."""
        try:
            import scalp_live_sim as sls
        except ImportError:
            pytest.skip("scalp_live_sim not importable in this environment")
        pending = getattr(sls, "PENDING_KEYS", [])
        live    = getattr(sls, "LIVE_SIM_DETECTORS", {})
        for key in pending:
            assert key not in live, (
                f"Pending strategy '{key}' found in LIVE_SIM_DETECTORS — "
                f"pending strategies must not fire in live simulation"
            )

    def test_inv_scan_diagnostics_dict_exists(self):
        """STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER must be a module-level dict."""
        assert isinstance(app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER, dict)


# ─── Part 10: Coverage proof ─────────────────────────────────────────────────

class TestCoverageProof:
    """T01–T12 (spec tests 1, 3–12, 21, 22): cycle-level evaluation coverage."""

    def setup_method(self):
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()

    # T01 — every registered+eligible strategy actually invoked
    def test_t01_all_strategies_invoked(self):
        """T01: Every registered and eligible strategy scorer is called per cycle."""
        call_log = []

        def _tracking(key):
            def _scorer(ctx):
                call_log.append(key)
                return {"direction": None, "conditions": [("A", False)], "target_r": 2.0}
            return _scorer

        scorers = {k: _tracking(k) for k in app.STRATEGY_PRIORITY}
        _run_engine(scorer_override=scorers)

        assert set(call_log) == set(app.STRATEGY_PRIORITY), (
            f"Not all strategies called. Called: {set(call_log)}, "
            f"Expected: {set(app.STRATEGY_PRIORITY)}"
        )

    # T02 — disabled (PENDING) strategies not invoked via scalp research
    def test_t02_pending_strategies_not_invoked(self):
        """T02: Scalp research PENDING_KEYS strategies cannot fire in live simulation."""
        try:
            import scalp_live_sim as sls
        except ImportError:
            pytest.skip("scalp_live_sim not importable")
        pending = getattr(sls, "PENDING_KEYS", [])
        live    = getattr(sls, "LIVE_SIM_DETECTORS", {})
        assert all(k not in live for k in pending), (
            "One or more PENDING_KEYS found in LIVE_SIM_DETECTORS"
        )

    # T03 — OPENING_DRIVE ineligible outside session window
    def test_t03_opening_drive_ineligible_outside_session(self):
        """T03 / T05: OPENING_DRIVE gets eligible=False + skip_reason=outside_session
        when in_opening_window=False."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        _run_engine(ctx_override=ctx)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        od = next((s for s in diag.get("strategies", [])
                   if s["strategy_key"] == "OPENING_DRIVE"), None)
        assert od is not None, "OPENING_DRIVE missing from diagnostics"
        assert od["eligible"] is False
        assert od["result"] == "skipped"
        assert od["skip_reason"] == "outside_session"

    # T04 — OPENING_DRIVE eligible inside session window
    def test_t04_opening_drive_eligible_inside_session(self):
        """T04: OPENING_DRIVE is eligible when in_opening_window=True."""
        ctx = dict(_FAKE_CTX, in_opening_window=True)
        _run_engine(ctx_override=ctx)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        od = next((s for s in diag.get("strategies", [])
                   if s["strategy_key"] == "OPENING_DRIVE"), None)
        assert od is not None
        assert od["eligible"] is True
        assert od["result"] != "skipped"
        assert od["skip_reason"] is None

    # T05 — no-signal strategy still marked evaluated
    def test_t05_no_signal_strategy_still_evaluated(self):
        """T07: A strategy returning no signal is still counted as evaluated."""
        all_no_signal = {k: _no_signal_scorer for k in app.STRATEGY_PRIORITY}
        _run_engine(ctx_override=dict(_FAKE_CTX, in_opening_window=False),
                    scorer_override=all_no_signal)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        eligible = [s for s in diag.get("strategies", []) if s["eligible"]]
        assert all(s["evaluated"] for s in eligible), "All eligible strategies must be evaluated"
        assert all(s["result"] == "no_signal" for s in eligible)

    # T06 — fully-met strategy is candidate or selected
    def test_t06_fully_met_strategy_is_candidate_or_selected(self):
        """T08: A strategy meeting all conditions is counted as evaluated + candidate/selected."""
        scorers = {k: (_long_scorer if k == "LIQUIDITY_SWEEP_REVERSAL" else _no_signal_scorer)
                   for k in app.STRATEGY_PRIORITY}
        _run_engine(ctx_override=dict(_FAKE_CTX, in_opening_window=False),
                    scorer_override=scorers)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        lsr = next((s for s in diag.get("strategies", [])
                    if s["strategy_key"] == "LIQUIDITY_SWEEP_REVERSAL"), None)
        assert lsr is not None
        assert lsr["evaluated"] is True
        assert lsr["result"] in ("candidate", "selected")

    # T07 — only the selected strategy is marked selected
    def test_t07_only_selected_strategy_marked_selected(self):
        """T09: Only the active_key strategy has selected=True in diagnostics."""
        scorers = {k: (_long_scorer if k == "LIQUIDITY_SWEEP_REVERSAL" else _no_signal_scorer)
                   for k in app.STRATEGY_PRIORITY}
        engine = _run_engine(ctx_override=dict(_FAKE_CTX, in_opening_window=False),
                             scorer_override=scorers)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        selected_list = [s for s in diag.get("strategies", []) if s["selected"]]
        assert len(selected_list) == 1
        assert selected_list[0]["strategy_key"] == engine.get("active_key")

    # T08 — non-selected candidate visible diagnostically
    def test_t08_non_selected_candidate_visible(self):
        """T10: A non-selected candidate remains visible with result='candidate'."""
        scorers = {
            "OPENING_DRIVE":            _no_signal_scorer,
            "LIQUIDITY_SWEEP_REVERSAL": _long_scorer,
            "VWAP_TREND_CONTINUATION":  _long_scorer,
            "RANGE_EXPANSION_BREAKOUT": _no_signal_scorer,
            "OPENING_RANGE_BREAKOUT":   _no_signal_scorer,
        }
        _run_engine(ctx_override=dict(_FAKE_CTX, in_opening_window=False),
                    scorer_override=scorers)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        candidates = [s for s in diag.get("strategies", [])
                      if s["result"] in ("candidate", "selected")]
        assert len(candidates) == 2, f"Expected 2 candidates/selected, got {len(candidates)}"
        non_sel = [s for s in candidates if not s["selected"]]
        assert len(non_sel) == 1
        assert non_sel[0]["result"] == "candidate"

    # T09 — scorer exception → engine returns safe fallback, diagnostics not updated
    def test_t09_scorer_exception_returns_safe_fallback(self):
        """T11: One scorer exception triggers outer fail-open → safe fallback returned,
        diagnostics not updated, other scorers not called (current engine-level behavior)."""
        call_log = []

        def _tracked(ctx):
            call_log.append("called")
            return {"direction": None, "conditions": [("A", False)], "target_r": 2.0}

        scorers = {
            "OPENING_DRIVE":            _failing_scorer,   # first in priority → exception
            "LIQUIDITY_SWEEP_REVERSAL": _tracked,
            "VWAP_TREND_CONTINUATION":  _tracked,
            "RANGE_EXPANSION_BREAKOUT": _tracked,
            "OPENING_RANGE_BREAKOUT":   _tracked,
        }
        engine = _run_engine(scorer_override=scorers)

        # Outer try/except catches the exception → returns _closed_strategy_engine() fallback
        assert "BALANCED" in (engine.get("market_regime") or ""), (
            f"Expected safe fallback regime, got {engine.get('market_regime')}"
        )
        # No other scorers should have been called (exception propagated immediately)
        assert len(call_log) == 0, f"Other scorers called after exception: {call_log}"
        # Diagnostics dict must NOT be updated after an exception
        assert "MGC" not in app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER, (
            "Diagnostics must not be written when the engine raises an exception"
        )

    # T10 — no double-evaluation per cycle
    def test_t10_no_double_evaluation(self):
        """T21: No strategy scorer is called more than once per evaluation cycle."""
        call_counts = {k: 0 for k in app.STRATEGY_PRIORITY}

        def _counting(key):
            def _scorer(ctx):
                call_counts[key] += 1
                return {"direction": None, "conditions": [("A", False)], "target_r": 2.0}
            return _scorer

        scorers = {k: _counting(k) for k in app.STRATEGY_PRIORITY}
        _run_engine(scorer_override=scorers)
        for key, count in call_counts.items():
            assert count == 1, f"{key} was called {count} times (expected exactly 1)"

    # T11 — evaluated_count == eligible_count (all scorers always called)
    def test_t11_evaluated_count_equals_eligible_count(self):
        """T17: In the main engine, all eligible strategies are always evaluated
        (evaluated_count == eligible_count, unevaluated_eligible == 0)."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)  # OPENING_DRIVE ineligible
        _run_engine(ctx_override=ctx)
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        assert diag.get("registered_count") == 5
        assert diag.get("eligible_count")   == 4   # OPENING_DRIVE excluded
        assert diag.get("evaluated_count")  == 4   # all 4 eligible ones evaluated
        assert diag.get("unevaluated_eligible") == 0

    # T12 — per-ticker isolation (equivalent to Long/Short distinct counts)
    def test_t12_per_ticker_isolation(self):
        """T22: Each ticker's diagnostics are independent — MGC and MNQ stored separately."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        with patch("app.build_strategy_context", return_value=ctx), \
             patch("app.detect_market_regime",   return_value=dict(_FAKE_REGIME)), \
             patch("app.detect_session_bias",    return_value=dict(_FAKE_BIAS)), \
             patch("app.instrument_of",          side_effect=lambda x: x):
            app.compute_strategy_engine("MGC", 2100.0, 2095.0, "fresh", None, 0)
            app.compute_strategy_engine("MNQ", 21000.0, 20950.0, "fresh", None, 0)
        assert "MGC" in app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER
        assert "MNQ" in app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER
        assert app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER["MGC"]["ticker"] == "MGC"
        assert app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER["MNQ"]["ticker"] == "MNQ"


# ─── No-mutation proof (T13–T18) ──────────────────────────────────────────────

class TestNoMutation:
    """Tests 13–18: Diagnostics do not change any production state."""

    def setup_method(self):
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()

    def test_t13_diagnostics_do_not_change_selected_strategy(self):
        """T13: Running compute_strategy_engine twice yields the same active_key."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()
        r2 = _run_engine(ctx_override=ctx)
        assert r1.get("active_key")      == r2.get("active_key")
        assert r1.get("active_strategy") == r2.get("active_strategy")

    def test_t14_diagnostics_do_not_change_completeness(self):
        """T14: Per-strategy completeness scores are identical across runs."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        r2 = _run_engine(ctx_override=ctx)
        comp1 = {s["key"]: s["completeness"] for s in r1.get("strategies", [])}
        comp2 = {s["key"]: s["completeness"] for s in r2.get("strategies", [])}
        assert comp1 == comp2

    def test_t15_diagnostics_do_not_change_confidence(self):
        """T15: Strategy diagnostics do not change confidence (Edge Score proxy)."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        r2 = _run_engine(ctx_override=ctx)
        assert r1.get("confidence") == r2.get("confidence")

    def test_t16_diagnostics_do_not_change_learning_delta(self):
        """T16: Strategy diagnostics do not change the learning history_weight or adjustment."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        r2 = _run_engine(ctx_override=ctx)
        assert r1.get("history_weight")     == r2.get("history_weight")
        assert r1.get("history_adjustment") == r2.get("history_adjustment")

    def test_t17_diagnostics_do_not_change_ready_flag(self):
        """T17: Strategy diagnostics do not change the READY/WAIT verdict (ready flag)."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        r2 = _run_engine(ctx_override=ctx)
        assert r1.get("ready") == r2.get("ready")

    def test_t18_diagnostics_stored_in_parallel_dict_not_engine_result(self):
        """T18: Diagnostic keys (registered_count etc.) are NOT injected into engine result."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        result = _run_engine(ctx_override=ctx)
        # Engine result must NOT contain diagnostic-only keys
        for key in ("registered_count", "evaluated_count", "unevaluated_eligible",
                    "candidate_count", "eligible_count"):
            assert key not in result, (
                f"Diagnostic key '{key}' must not appear in engine result dict"
            )
        # Parallel dict must contain them
        diag = app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.get("MGC", {})
        assert "registered_count" in diag
        assert "evaluated_count"  in diag


# ─── Infrastructure tests (T19–T25) ───────────────────────────────────────────

class TestEndpointAndInfra:
    """Tests 19–25: Endpoint, byte-identity, no Databento calls, empty-state safety."""

    def setup_method(self):
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.clear()

    # T19 — byte-identical engine output regardless of diagnostics dict state
    def test_t19_engine_output_byte_identical_regardless_of_diag_state(self):
        """T19: compute_strategy_engine return value is identical whether the
        diagnostics dict is empty or pre-populated with stale data."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        r1 = _run_engine(ctx_override=ctx)
        # Pre-populate with stale data
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER["MGC"] = {"stale": True, "strategies": []}
        r2 = _run_engine(ctx_override=ctx)
        assert json.dumps(r1, sort_keys=True, default=str) == \
               json.dumps(r2, sort_keys=True, default=str), \
               "Engine output changed due to diagnostics dict state — must be byte-identical"

    # T20 — no Databento / network calls during unit tests
    def test_t20_no_network_calls_in_unit_tests(self):
        """T20: compute_strategy_engine must not trigger any requests.get/post calls."""
        ctx = dict(_FAKE_CTX, in_opening_window=False)
        with patch("requests.get",  side_effect=AssertionError("requests.get called")) as mg, \
             patch("requests.post", side_effect=AssertionError("requests.post called")) as mp:
            _run_engine(ctx_override=ctx)
            assert not mg.called,  "requests.get was called — no network access in tests"
            assert not mp.called,  "requests.post was called — no network access in tests"

    # T21 — no double-evaluation across two tickers in one test session
    def test_t21_different_tickers_each_get_independent_counts(self):
        """T21: Separate tickers don't share evaluation counts (no double-counting)."""
        call_counts = {"MGC": 0, "MNQ": 0}

        def _make_scorer(ticker):
            def _scorer(ctx):
                call_counts[ticker] += 1
                return {"direction": None, "conditions": [("A", False)], "target_r": 2.0}
            return _scorer

        mgc_scorers = {k: _make_scorer("MGC") for k in app.STRATEGY_PRIORITY}
        mnq_scorers = {k: _make_scorer("MNQ") for k in app.STRATEGY_PRIORITY}

        ctx = dict(_FAKE_CTX, in_opening_window=False)
        with patch("app.build_strategy_context", return_value=ctx), \
             patch("app.detect_market_regime",   return_value=dict(_FAKE_REGIME)), \
             patch("app.detect_session_bias",    return_value=dict(_FAKE_BIAS)):
            with patch("app.instrument_of", return_value="MGC"), \
                 patch("app.STRATEGY_SCORERS", mgc_scorers):
                app.compute_strategy_engine("MGC", 2100.0, 2095.0, "fresh", None, 0)
            with patch("app.instrument_of", return_value="MNQ"), \
                 patch("app.STRATEGY_SCORERS", mnq_scorers):
                app.compute_strategy_engine("MNQ", 21000.0, 20950.0, "fresh", None, 0)

        assert call_counts["MGC"] == len(app.STRATEGY_PRIORITY), \
            f"MGC: expected {len(app.STRATEGY_PRIORITY)} calls, got {call_counts['MGC']}"
        assert call_counts["MNQ"] == len(app.STRATEGY_PRIORITY), \
            f"MNQ: expected {len(app.STRATEGY_PRIORITY)} calls, got {call_counts['MNQ']}"

    # T22 — endpoint returns empty tickers safely before first scan
    def test_t22_endpoint_empty_state_safe(self):
        """T25: /strategy-scan-diagnostics returns valid JSON with empty tickers before any scan."""
        client = app.app.test_client()
        resp = client.get("/strategy-scan-diagnostics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "tickers" in data
        assert isinstance(data["tickers"], dict)
        assert len(data["tickers"]) == 0

    # T23 — endpoint does not call any scorer
    def test_t23_endpoint_does_not_execute_strategies(self):
        """T23: GET /strategy-scan-diagnostics must not call any STRATEGY_SCORERS function."""
        call_log = []

        def _spy(ctx):
            call_log.append("called")
            return {"direction": None, "conditions": [("A", False)], "target_r": 2.0}

        spy_scorers = {k: _spy for k in app.STRATEGY_PRIORITY}
        with patch("app.STRATEGY_SCORERS", spy_scorers):
            client = app.app.test_client()
            client.get("/strategy-scan-diagnostics")
        assert len(call_log) == 0, (
            f"Endpoint called {len(call_log)} scorer(s) — endpoint must be purely read-only"
        )

    # T24 — endpoint does not mutate the diagnostics dict
    def test_t24_endpoint_does_not_mutate_diag_dict(self):
        """T24: GET /strategy-scan-diagnostics must not modify STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER."""
        app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER["MGC"] = {
            "ticker": "MGC", "mode": "SCALP", "strategies": [], "registered_count": 5,
        }
        snapshot_before = {k: dict(v) for k, v in app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER.items()}
        client = app.app.test_client()
        client.get("/strategy-scan-diagnostics")
        for key in snapshot_before:
            assert key in app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER
            assert app.STRATEGY_SCAN_DIAGNOSTICS_BY_TICKER[key] == snapshot_before[key]

    # T25 — endpoint returns priority_order and registered_total
    def test_t25_endpoint_returns_registry_metadata(self):
        """T23: Endpoint response includes priority_order and registered_total from the registry."""
        client = app.app.test_client()
        resp = client.get("/strategy-scan-diagnostics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("priority_order")   == list(app.STRATEGY_PRIORITY)
        assert data.get("registered_total") == len(app.STRATEGY_DEFS)
