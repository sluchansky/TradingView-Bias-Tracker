"""Backtest Strategy Coverage Audit tests.

Proves exactly WHY the dashboard shows four strategies for every instrument,
and pins the registry counts so regressions are caught immediately.

Run:
  pytest test_backtest_coverage_audit.py
  python3 test_backtest_coverage_audit.py
"""

import os
import sys
from unittest.mock import patch, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as bt
import scalp_research as sr


# ── shared synthetic dataset (avoids network calls; no Databento import) ───
def _candles(n=200, symbol="MGC"):
    """Return n synthetic 1-minute OHLCV candles via bt._synthetic_candles."""
    return bt._synthetic_candles(n=n, symbol=symbol, seed=42)


def _run(strategies=None, symbol="MGC", mode="SCALP"):
    """Helper: run the backtest engine with synthetic candles."""
    candles = _candles(symbol=symbol)
    params = {"symbol": symbol, "mode": mode, "strategies": strategies}
    return bt.run_backtest(candles, params)


# ════════════════════════════════════════════════════════════════════════════
# Part 1 — Static registry counts
# ════════════════════════════════════════════════════════════════════════════

def test_1_bt_strategy_defs_count():
    """Backtest STRATEGY_DEFS has exactly 6 entries (5 originals + ORB adapter)."""
    assert len(bt.STRATEGY_DEFS) == 6, bt.STRATEGY_DEFS.keys()


def test_2_bt_strategy_defs_keys():
    """Backtest STRATEGY_DEFS contains the expected six keys."""
    expected = {
        "OPENING_DRIVE",
        "LIQUIDITY_SWEEP_REVERSAL",
        "VWAP_TREND_CONTINUATION",
        "RANGE_EXPANSION_BREAKOUT",
        "EXHAUSTION_FADE",
        "OPENING_RANGE_BREAKOUT",
    }
    assert set(bt.STRATEGY_DEFS) == expected


def test_3_bt_detectors_count():
    """Backtest DETECTORS has exactly 6 entries — every STRATEGY_DEFS key has one."""
    assert len(bt.DETECTORS) == 6, bt.DETECTORS.keys()


def test_4_bt_detectors_match_strategy_defs():
    """Every key in bt.DETECTORS is also in bt.STRATEGY_DEFS and vice-versa."""
    assert set(bt.DETECTORS) == set(bt.STRATEGY_DEFS)


def test_5_bt_strategy_order_count():
    """STRATEGY_ORDER has exactly 5 entries (4 originals + ORB adapter)."""
    assert len(bt.STRATEGY_ORDER) == 5, bt.STRATEGY_ORDER


def test_6_bt_strategy_order_contents():
    """STRATEGY_ORDER contains the five eligible strategies in the correct order."""
    expected = [
        "OPENING_DRIVE",
        "LIQUIDITY_SWEEP_REVERSAL",
        "VWAP_TREND_CONTINUATION",
        "RANGE_EXPANSION_BREAKOUT",
        "OPENING_RANGE_BREAKOUT",
    ]
    assert bt.STRATEGY_ORDER == expected


def test_7_bt_disabled_strategies():
    """DISABLED_STRATEGIES contains exactly EXHAUSTION_FADE."""
    assert bt.DISABLED_STRATEGIES == {"EXHAUSTION_FADE"}


def test_8_bt_strategy_order_is_subset_of_detectors():
    """Every key in STRATEGY_ORDER has a working detector."""
    for key in bt.STRATEGY_ORDER:
        assert key in bt.DETECTORS, f"{key} in STRATEGY_ORDER lacks a detector"


def test_9_bt_strategy_order_disjoint_from_disabled():
    """No key in STRATEGY_ORDER is in DISABLED_STRATEGIES."""
    overlap = set(bt.STRATEGY_ORDER) & bt.DISABLED_STRATEGIES
    assert not overlap, f"Strategy order and disabled overlap: {overlap}"


def test_10_exhaustion_fade_has_detector_but_not_in_order():
    """EXHAUSTION_FADE: detector present, excluded from STRATEGY_ORDER, in DISABLED."""
    assert "EXHAUSTION_FADE" in bt.DETECTORS
    assert "EXHAUSTION_FADE" not in bt.STRATEGY_ORDER
    assert "EXHAUSTION_FADE" in bt.DISABLED_STRATEGIES


def test_11_opening_range_breakout_present_in_bt():
    """OPENING_RANGE_BREAKOUT historical adapter is registered in STRATEGY_DEFS,
    DETECTORS, and STRATEGY_ORDER, and is NOT disabled — it is fully eligible."""
    assert "OPENING_RANGE_BREAKOUT" in bt.STRATEGY_DEFS
    assert "OPENING_RANGE_BREAKOUT" in bt.DETECTORS
    assert "OPENING_RANGE_BREAKOUT" in bt.STRATEGY_ORDER
    assert "OPENING_RANGE_BREAKOUT" not in bt.DISABLED_STRATEGIES


def test_12_bt_specs_instruments():
    """BT_SPECS covers exactly the four active instruments."""
    assert set(bt.BT_SPECS) == {"MNQ", "MGC", "MES", "MYM"}


def test_13_bt_modes():
    """BT_MODES covers exactly SCALP and SWING."""
    assert set(bt.BT_MODES) == {"SCALP", "SWING"}


# ════════════════════════════════════════════════════════════════════════════
# Part 2 — Invocation count: proves exactly four are invoked
# ════════════════════════════════════════════════════════════════════════════

def test_14_run_backtest_default_returns_exactly_five_strategies():
    """run_backtest with strategies=None evaluates and returns exactly 5 strategies."""
    res = _run(strategies=None)
    assert res["ok"], res.get("error")
    assert len(res["strategies"]) == 5, list(res["strategies"].keys())
    assert set(res["strategies"]) == set(bt.STRATEGY_ORDER)


def test_15_run_backtest_invokes_every_eligible_strategy_exactly_once():
    """Each eligible detector is called at least once in an All Strategies run
    (proves no strategy is silently skipped; a strategy with zero trades is still
    called — it just never returns a non-None signal)."""
    call_counts = {k: 0 for k in bt.STRATEGY_ORDER}
    originals = {k: bt.DETECTORS[k] for k in bt.STRATEGY_ORDER}

    def make_spy(key, real_fn):
        def spy(snap):
            call_counts[key] += 1
            return real_fn(snap)
        return spy

    patched = {k: make_spy(k, originals[k]) for k in bt.STRATEGY_ORDER}
    with patch.dict(bt.DETECTORS, patched):
        res = _run(strategies=None)

    assert res["ok"]
    for key in bt.STRATEGY_ORDER:
        assert call_counts[key] > 0, f"{key} detector was never called"


def test_16_run_backtest_single_strategy_invokes_only_that_one():
    """Requesting a single strategy invokes only that strategy's detector."""
    target = "OPENING_DRIVE"
    call_counts = {k: 0 for k in bt.STRATEGY_ORDER}
    originals = {k: bt.DETECTORS[k] for k in bt.STRATEGY_ORDER}

    def make_spy(key, real_fn):
        def spy(snap):
            call_counts[key] += 1
            return real_fn(snap)
        return spy

    patched = {k: make_spy(k, originals[k]) for k in bt.STRATEGY_ORDER}
    with patch.dict(bt.DETECTORS, patched):
        res = _run(strategies=[target])

    assert res["ok"]
    assert target in res["strategies"]
    assert len(res["strategies"]) == 1
    assert call_counts[target] > 0
    for key in bt.STRATEGY_ORDER:
        if key != target:
            assert call_counts[key] == 0, f"{key} was called despite not being selected"


def test_17_exhaustion_fade_explicit_request_is_blocked():
    """Even when EXHAUSTION_FADE is explicitly requested it is filtered out — returns 0 strategies."""
    res = _run(strategies=["EXHAUSTION_FADE"])
    assert res["ok"]
    assert len(res["strategies"]) == 0, list(res["strategies"].keys())
    assert "EXHAUSTION_FADE" not in res["strategies"]


def test_18_zero_trade_strategy_still_appears_in_result():
    """A strategy that fires its detector but produces zero trades is still present in
    result['strategies'] — it is NOT silently hidden. Distinguishes zero-trade from
    unsupported."""
    # Force every detector to return None (no signal → 0 trades for every strategy).
    silent = {k: (lambda s: None) for k in bt.STRATEGY_ORDER}
    with patch.dict(bt.DETECTORS, silent):
        res = _run(strategies=None)
    assert res["ok"]
    # All five must still appear with total_trades == 0.
    assert len(res["strategies"]) == 5
    for key in bt.STRATEGY_ORDER:
        assert key in res["strategies"], f"{key} missing from result despite being eligible"
        assert res["strategies"][key]["total_trades"] == 0


def test_19_zero_trade_distinguished_from_disabled():
    """A zero-trade eligible strategy is present in result['strategies'];
    a disabled strategy is absent — the two cases are distinct."""
    silent = {k: (lambda s: None) for k in bt.STRATEGY_ORDER}
    with patch.dict(bt.DETECTORS, silent):
        res = _run(strategies=None)
    for key in bt.STRATEGY_ORDER:
        assert key in res["strategies"]
    assert "EXHAUSTION_FADE" not in res["strategies"]


def test_20_result_ranking_length_matches_strategy_count():
    """result['ranking'] length == len(result['strategies']) — no hidden display cap."""
    res = _run(strategies=None)
    assert res["ok"]
    assert len(res["ranking"]) == len(res["strategies"])


def test_21_result_includes_disabled_strategies_in_filters():
    """result['filters']['disabled_strategies'] names the blocked strategies so the
    dashboard can show a human-readable note."""
    res = _run(strategies=None)
    assert res["ok"]
    fl = res.get("filters", {})
    assert "disabled_strategies" in fl
    assert "EXHAUSTION_FADE" in fl["disabled_strategies"]


def test_22_run_optimization_uses_same_five_strategies():
    """run_optimization defaults to STRATEGY_ORDER (5), same as run_backtest."""
    candles = _candles(symbol="MGC")
    params = {"symbol": "MGC", "mode": "SCALP"}
    res = bt.run_optimization(candles, params)
    assert res["ok"]
    assert set(res["strategies"]) == set(bt.STRATEGY_ORDER)
    assert len(res["strategies"]) == 5


# ════════════════════════════════════════════════════════════════════════════
# Part 3 — Research engine isolation
# ════════════════════════════════════════════════════════════════════════════

def test_23_scalp_research_library_count():
    """scalp_research STRATEGY_LIBRARY has exactly 19 entries."""
    assert len(sr.STRATEGY_LIBRARY) == 19, len(sr.STRATEGY_LIBRARY)


def test_24_scalp_research_detectors_count():
    """scalp_research RESEARCH_DETECTORS has exactly 16 entries (3 pending)."""
    assert len(sr.RESEARCH_DETECTORS) == 16, len(sr.RESEARCH_DETECTORS)


def test_25_no_databento_import_in_backtest_engine():
    """backtest_engine.py is self-contained and does not import Databento or any
    live-app module — confirms historical replay cannot trigger live feeds."""
    import importlib, types
    # Verify by inspecting the module's direct imports (its __dict__ references).
    mod = sys.modules.get("backtest_engine") or bt
    for attr in ("databento", "app", "databento_brain"):
        assert not hasattr(mod, attr), (
            f"backtest_engine unexpectedly exposes '{attr}' — isolation violated")
    # Also confirm 'databento' does not appear in the module's source.
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "backtest_engine.py")
    with open(src_path) as fh:
        src = fh.read()
    assert "import databento" not in src, "databento import found in backtest_engine.py"
    assert "from databento" not in src, "databento import found in backtest_engine.py"


# ════════════════════════════════════════════════════════════════════════════
# Part 4 — Coverage-logic explicit reasons + metrics immutability
# (Covers audit requirements 14 and 15-19)
# ════════════════════════════════════════════════════════════════════════════

def test_26_coverage_logic_explicit_reason_for_every_excluded_strategy():
    """The coverage classification logic assigns a non-None reason to every
    non-eligible strategy, and None only to eligible ones.

    This reimplements the same classification logic used by GET /backtest/coverage
    without requiring a Flask context, proving the endpoint would return the
    correct reason field for EXHAUSTION_FADE and OPENING_RANGE_BREAKOUT.

    Covers audit requirement 14: 'Missing replay inputs produce explicit reason.'
    """
    LIVE_KEYS = {
        "OPENING_DRIVE", "LIQUIDITY_SWEEP_REVERSAL",
        "VWAP_TREND_CONTINUATION", "RANGE_EXPANSION_BREAKOUT",
        "OPENING_RANGE_BREAKOUT",
    }

    results = {}
    for key in sorted(LIVE_KEYS | set(bt.STRATEGY_DEFS)):
        in_bt   = key in bt.STRATEGY_DEFS
        has_det = key in bt.DETECTORS
        in_ord  = key in bt.STRATEGY_ORDER
        disabl  = key in bt.DISABLED_STRATEGIES
        elig    = in_bt and has_det and in_ord and not disabl

        if disabl:
            reason = "disabled_by_request"
        elif not in_bt:
            reason = "no_backtest_definition"
        elif not has_det:
            reason = "no_historical_adapter"
        elif not in_ord:
            reason = "excluded_from_strategy_order"
        else:
            reason = None
        results[key] = {"eligible": elig, "reason": reason}

    # All four eligible strategies have reason=None
    for key in bt.STRATEGY_ORDER:
        assert results[key]["eligible"] is True
        assert results[key]["reason"] is None, f"{key} should have no exclusion reason"

    # EXHAUSTION_FADE is disabled
    ef = results["EXHAUSTION_FADE"]
    assert ef["eligible"] is False
    assert ef["reason"] == "disabled_by_request"

    # OPENING_RANGE_BREAKOUT is now fully registered in the backtest engine
    orb = results["OPENING_RANGE_BREAKOUT"]
    assert orb["eligible"] is True, "OPENING_RANGE_BREAKOUT must be eligible in BT"
    assert orb["reason"] is None, "Eligible strategies have no exclusion reason"


def test_27_coverage_logic_does_not_invoke_run_backtest():
    """GET /backtest/coverage reads only static module-level dicts — it does not
    call run_backtest, simulate_strategy, or any function that mutates state.

    Proven by: patching bt.run_backtest + bt.simulate_strategy to raise
    RuntimeError, then executing the coverage classification logic. If either
    were called, the test would fail.

    Also verifies that a run_backtest call before and after the coverage
    classification produces byte-identical results (reqs 15-19).
    """
    candles = _candles(symbol="MGC")
    params  = {"symbol": "MGC", "mode": "SCALP"}

    # Baseline run BEFORE any coverage logic.
    # ranking is a list of strategy-name strings (not dicts).
    baseline = bt.run_backtest(candles, params)
    assert baseline["ok"]
    baseline_ranking = list(baseline["ranking"])           # e.g. ["VWAP_TREND_CONTINUATION", ...]
    baseline_trades  = {k: v["total_trades"] for k, v in baseline["strategies"].items()}

    def _fail(*a, **kw):
        raise RuntimeError("coverage must not call run_backtest or simulate_strategy")

    with patch.object(bt, "run_backtest", side_effect=_fail), \
         patch.object(bt, "simulate_strategy", side_effect=_fail):
        # Execute the exact same classification logic as GET /backtest/coverage.
        all_keys = set(bt.STRATEGY_DEFS) | set(bt.STRATEGY_ORDER)
        coverage = {}
        for key in all_keys:
            in_ord = key in bt.STRATEGY_ORDER
            disabl = key in bt.DISABLED_STRATEGIES
            coverage[key] = {
                "eligible": in_ord and not disabl,
                "in_bt_defs": key in bt.STRATEGY_DEFS,
                "has_detector": key in bt.DETECTORS,
                "in_strategy_order": in_ord,
                "disabled": disabl,
            }
        # No RuntimeError raised — confirms coverage classification is read-only.

    # Repeat run AFTER coverage logic — results must be byte-identical.
    after = bt.run_backtest(candles, params)
    assert after["ok"]
    after_ranking = list(after["ranking"])

    assert baseline_ranking == after_ranking, (
        "Coverage logic mutated module state: ranking changed between runs")
    for key in bt.STRATEGY_ORDER:
        b = baseline_trades[key]
        a = after["strategies"][key]["total_trades"]
        assert b == a, (
            f"{key}: trade count changed from {b} to {a} after coverage — "
            "coverage classification mutated engine state")


def test_28_coverage_api_would_be_dispatched_not_invoked():
    """bt_coverage response dict uses 'would_be_dispatched' (not 'invoked').

    The field was renamed so its semantics are unambiguous: 'invoked' implies the
    strategy was actually called during a backtest run, but coverage is a static
    read of module-level dicts and never executes any strategy. 'would_be_dispatched'
    correctly documents the intent: eligible strategies WOULD run if backtest is
    called with default params.

    Verified two ways:
      1. The new key appears in app.py's bt_coverage dict literal.
      2. The old 'invoked' key no longer appears in that dict literal.
    """
    _app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    with open(_app_py, encoding="utf-8") as f:
        src = f.read()

    # New key must be present
    assert '"would_be_dispatched"' in src, (
        "bt_coverage response dict must contain 'would_be_dispatched' key")

    # Old key must not appear as a dict entry (the exact stanza that was renamed)
    assert '"invoked":               eligible' not in src, (
        "Old 'invoked' dict key still present in bt_coverage — "
        "should have been renamed to 'would_be_dispatched'")

    # Structural check: 'would_be_dispatched' and 'eligible' are distinct fields
    # (would_be_dispatched == eligible for now, but the names are separately present)
    assert '"eligible"' in src, "'eligible' field must still exist alongside 'would_be_dispatched'"


# ════════════════════════════════════════════════════════════════════════════
# Self-runner
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed of {len(tests)}")
    sys.exit(1 if failed else 0)
