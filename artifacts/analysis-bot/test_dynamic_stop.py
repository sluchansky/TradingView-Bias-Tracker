"""ATR(14)-based dynamic stop-loss tests.

Pure-function tests for `_dynamic_stop_plan` and `build_strict_trade_plan`. They
exercise the architect's required cases: multiplier precedence (volatility wins),
structure-vs-ATR (wider wins), the per-instrument min-tick floor, directional
safety, MGC/MNQ symmetry, risk-dollar math, ATR-unavailable -> no plan (WAIT),
and the ENFORCE_MIN_RR veto (SWING vetoes <1:2, SCALP only displays it).

Runnable two ways:
  • pytest test_dynamic_stop.py
  • python3 test_dynamic_stop.py     (no pytest needed — built-in runner)

No network/threads: importing app.py is side-effect-free (schedulers start only
under `__main__`), and every test passes volatility + nearest levels explicitly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


def _vol(status="ok", atr=2.0, regime="NORMAL", label="Normal", ratio=1.0):
    return {"status": status, "atr_pts": atr, "regime": regime,
            "label": label, "ratio": ratio}


# ── _dynamic_stop_plan ──────────────────────────────────────────────────────

def test_atr_unavailable_status_not_ok():
    r = app._dynamic_stop_plan("Long", 2000.0, 1990.0, 2010.0, "MGC",
                               _vol(status="stale"), "SCALP")
    assert r["ok"] is False
    assert r["reason"] == "ATR stop data unavailable"


def test_atr_unavailable_missing_value():
    r = app._dynamic_stop_plan("Long", 2000.0, 1990.0, 2010.0, "MGC",
                               _vol(atr=None), "SCALP")
    assert r["ok"] is False
    assert r["reason"] == "ATR stop data unavailable"


def test_atr_zero_or_negative_rejected():
    for bad in (0, -1.5):
        r = app._dynamic_stop_plan("Long", 2000.0, 1990.0, 2010.0, "MGC",
                                   _vol(atr=bad), "SCALP")
        assert r["ok"] is False


def test_multiplier_volatility_wins_over_mode():
    # HIGH_CAUTION/HIGH_BLOCK -> 2.0 regardless of mode (volatility wins).
    for regime in ("HIGH_CAUTION", "HIGH_BLOCK"):
        for mode in ("SCALP", "SWING"):
            r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                       _vol(atr=2.0, regime=regime), mode)
            assert r["ok"] is True
            assert r["multiplier"] == 2.0


def test_multiplier_scalp_normal_is_1():
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SCALP")
    assert r["multiplier"] == 1.0


def test_multiplier_swing_normal_is_1_5():
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SWING")
    assert r["multiplier"] == 1.5


def test_structure_wider_than_atr_wins_long():
    # ATR stop = 1998, structure stop = 1989 (further below) -> structure wins.
    r = app._dynamic_stop_plan("Long", 2000.0, 1990.0, None, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 1998.0
    assert r["structure_stop"] == 1989.0
    assert r["calculated_stop"] == 1989.0


def test_atr_wider_than_structure_wins_long():
    # ATR stop = 1980 (further below) vs structure stop = 1994 -> ATR wins.
    r = app._dynamic_stop_plan("Long", 2000.0, 1995.0, None, "MGC",
                               _vol(atr=20.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 1980.0
    assert r["structure_stop"] == 1994.0
    assert r["calculated_stop"] == 1980.0


def test_structure_wider_than_atr_wins_short():
    # Short: structure stop = nearest_supply + buf, wider = further ABOVE.
    r = app._dynamic_stop_plan("Short", 2000.0, None, 2010.0, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 2002.0
    assert r["structure_stop"] == 2011.0
    assert r["calculated_stop"] == 2011.0


def test_min_tick_floor_applied_mgc():
    # Tiny ATR, no structure -> calculated stop is far tighter than the 50-tick
    # MGC floor, so the floor must kick in.
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=0.2, regime="NORMAL"), "SCALP")
    assert r["ok"] is True
    assert r["min_stop_ticks"] == 50
    assert r["stop_distance_ticks"] == 50          # floored
    assert r["min_floor_applied"] is True
    assert round(r["risk_points"], 4) == 5.0       # 50 ticks * 0.1
    assert r["final_stop"] == 1995.0


def test_long_stop_below_entry_short_stop_above():
    rl = app._dynamic_stop_plan("Long", 2000.0, 1990.0, 2010.0, "MGC",
                                _vol(atr=2.0), "SCALP")
    rs = app._dynamic_stop_plan("Short", 2000.0, 1990.0, 2010.0, "MGC",
                                _vol(atr=2.0), "SCALP")
    assert rl["final_stop"] < 2000.0
    assert rs["final_stop"] > 2000.0


def test_symmetry_specs_mgc_mnq():
    rg = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                _vol(atr=0.2), "SCALP")
    rq = app._dynamic_stop_plan("Long", 20000.0, None, None, "MNQ",
                                _vol(atr=0.2), "SCALP")
    assert (rg["min_stop_ticks"], rg["tick_size"]) == (50, 0.1)
    assert (rq["min_stop_ticks"], rq["tick_size"]) == (40, 0.25)


def test_risk_dollars_uses_point_value():
    rg = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                _vol(atr=0.2), "SCALP")   # 5.0 pts floor
    rq = app._dynamic_stop_plan("Long", 20000.0, None, None, "MNQ",
                                _vol(atr=0.2), "SCALP")   # 10.0 pts floor
    assert rg["risk_dollars"] == round(rg["risk_points"] * 10.0, 2)   # MGC $10/pt
    assert rq["risk_dollars"] == round(rq["risk_points"] * 2.0, 2)    # MNQ $2/pt


# ── build_strict_trade_plan ─────────────────────────────────────────────────

def test_plan_waits_when_atr_unavailable():
    p = app.build_strict_trade_plan("Long", "MGC", 2000.0, 2010.0, 1990.0,
                                    volatility=_vol(status="stale"), mode="SCALP")
    assert p["trade_plan"] is False
    assert p["reason"] == "ATR stop data unavailable"
    # No-plan dict keeps ATR metadata keys present (None) for reader parity.
    for k in ("atr_pts", "atr_multiplier", "stop_distance_ticks",
              "risk_dollars_per_contract", "min_floor_applied"):
        assert k in p and p[k] is None


def test_plan_success_long_surfaces_metadata():
    p = app.build_strict_trade_plan("Long", "MGC", 2000.0, 2010.0, 1990.0,
                                    volatility=_vol(atr=2.0, regime="NORMAL"),
                                    mode="SCALP")
    assert p["trade_plan"] is True
    assert p["atr_pts"] == 2.0
    assert p["atr_multiplier"] == 1.0
    assert p["min_stop_ticks"] == 50
    assert p["stop_distance_ticks"] >= 50
    assert p["risk_dollars_per_contract"] > 0
    assert p["nearest_demand"] == 1990.0
    assert float(p["stop_loss"]) < 2000.0          # below entry (Long)


def test_plan_success_short_stop_above_entry():
    p = app.build_strict_trade_plan("Short", "MNQ", 20000.0, 20010.0, 19990.0,
                                    volatility=_vol(atr=5.0, regime="NORMAL"),
                                    mode="SCALP")
    assert p["trade_plan"] is True
    assert float(p["stop_loss"]) > 20010.0         # above the supply anchor entry


def _with_mode(mode, fn):
    saved = app.TRADING_MODE
    app.TRADING_MODE = mode
    try:
        return fn()
    finally:
        app.TRADING_MODE = saved


def test_enforce_min_rr_swing_vetoes_low_rr():
    # Wide ATR makes the fixed TP2 < 1:2; SWING (ENFORCE_MIN_RR) must veto -> WAIT.
    def run():
        return app.build_strict_trade_plan(
            "Long", "MGC", 2000.0, 2010.0, 1990.0,
            volatility=_vol(atr=10.0, regime="NORMAL"), mode="SWING")
    p = _with_mode("SWING", run)
    assert p["trade_plan"] is False
    assert "1:2" in p["reason"]


def test_scalp_displays_low_rr_without_veto():
    # Same wide stop in SCALP: R:R shown, never gated -> plan still valid.
    def run():
        return app.build_strict_trade_plan(
            "Long", "MGC", 2000.0, 2010.0, 1990.0,
            volatility=_vol(atr=10.0, regime="NORMAL"), mode="SCALP")
    p = _with_mode("SCALP", run)
    assert p["trade_plan"] is True
    assert p["rr"] is not None


# ── built-in runner (no pytest dependency) ──────────────────────────────────

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
