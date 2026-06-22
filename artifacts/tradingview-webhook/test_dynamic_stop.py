"""ATR(14)-based dynamic stop-loss tests.

Pure-function tests for `_dynamic_stop_plan` and `build_strict_trade_plan`. They
exercise: multiplier precedence (VOLATILITY WINS over mode, mode-tunable knobs),
structure-vs-ATR (wider wins), the per-instrument HARD minimum-stop guard
(too-tight stops are REJECTED outright — no silent widening, so there is no longer
an artificial tick "floor"), directional safety, MGC/MNQ symmetry, risk-dollar
math, ATR-unavailable -> no plan (WAIT), and the FIXED 1:1 R:R model (every plan is
exactly 1:1; the legacy "<1:2 on TP2 -> no trade" veto no longer fires in either
mode — ENFORCE_MIN_RR is retained config but unused).

Mode profiles under test (SCALP is the retuned "cut losers fast" profile; SWING is
unchanged / byte-for-byte):
  • SCALP: multiplier 0.75 / 1.25; MGC min 3 pts / 30 ticks; MNQ min 10 pts / 40 ticks.
  • SWING: multiplier 1.5  / 2.0 ; MGC min 5 pts / 50 ticks; MNQ min 20 pts / 40 ticks.

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
    # Elevated regime forces the mode's HIGH multiplier (volatility wins over mode):
    # SCALP 1.25, SWING 2.0 — each larger than that mode's base multiplier. (atr=10
    # keeps the resulting stop above both modes' MGC minimums so it isn't rejected.)
    expected = {"SCALP": 1.25, "SWING": 2.0}
    for regime in ("HIGH_CAUTION", "HIGH_BLOCK"):
        for mode in ("SCALP", "SWING"):
            r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                       _vol(atr=10.0, regime=regime), mode)
            assert r["ok"] is True
            assert r["multiplier"] == expected[mode]


def test_multiplier_scalp_normal_is_0_75():
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=10.0, regime="NORMAL"), "SCALP")
    assert r["ok"] is True
    assert r["multiplier"] == 0.75


def test_multiplier_swing_normal_is_1_5():
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=10.0, regime="NORMAL"), "SWING")
    assert r["ok"] is True
    assert r["multiplier"] == 1.5


def test_structure_wider_than_atr_wins_long():
    # SCALP mult 0.75 -> ATR stop = 1998.5; structure stop = 1989.0 (further below)
    # is the safer/wider stop and wins.
    r = app._dynamic_stop_plan("Long", 2000.0, 1990.0, None, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 1998.5
    assert r["structure_stop"] == 1989.0
    assert r["calculated_stop"] == 1989.0


def test_atr_wider_than_structure_wins_long():
    # SCALP mult 0.75 -> ATR stop = 1985.0 (further below) vs structure stop = 1994.0.
    r = app._dynamic_stop_plan("Long", 2000.0, 1995.0, None, "MGC",
                               _vol(atr=20.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 1985.0
    assert r["structure_stop"] == 1994.0
    assert r["calculated_stop"] == 1985.0


def test_structure_wider_than_atr_wins_short():
    # Short: structure stop = nearest_supply + buf, wider = further ABOVE.
    r = app._dynamic_stop_plan("Short", 2000.0, None, 2010.0, "MGC",
                               _vol(atr=2.0, regime="NORMAL"), "SCALP")
    assert r["atr_stop"] == 2001.5
    assert r["structure_stop"] == 2011.0
    assert r["calculated_stop"] == 2011.0


def test_scalp_hard_min_stop_rejects_too_tight_mgc():
    # No silent widening: a stop tighter than the SCALP MGC minimum (3 pts) is
    # REJECTED outright. atr 0.2 * 0.75 = 0.15 pts << 3.0 -> ok False.
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=0.2, regime="NORMAL"), "SCALP")
    assert r["ok"] is False
    assert "too tight" in r["reason"]


def test_scalp_mgc_valid_stop_snaps_and_surfaces_metadata():
    # atr 5.0 * 0.75 = 3.75 pts (> 3.0 min) -> ok; snaps UP to whole ticks (0.1, so
    # 37.5 -> 38) and surfaces the SCALP MGC min_stop_ticks (30) as metadata. The
    # minimum is enforced by REJECTION (above), not by flooring this distance up.
    r = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                               _vol(atr=5.0, regime="NORMAL"), "SCALP")
    assert r["ok"] is True
    assert r["multiplier"] == 0.75
    assert r["min_stop_ticks"] == 30
    assert r["stop_distance_ticks"] == 38          # ceil(37.5)
    assert round(r["risk_points"], 4) == 3.8
    assert r["final_stop"] == 1996.2


def test_long_stop_below_entry_short_stop_above():
    rl = app._dynamic_stop_plan("Long", 2000.0, 1990.0, 2010.0, "MGC",
                                _vol(atr=2.0), "SCALP")
    rs = app._dynamic_stop_plan("Short", 2000.0, 1990.0, 2010.0, "MGC",
                                _vol(atr=2.0), "SCALP")
    assert rl["final_stop"] < 2000.0
    assert rs["final_stop"] > 2000.0


def test_symmetry_specs_mgc_mnq():
    # SCALP min_stop_ticks metadata + tick size: MGC 30 @ 0.1, MNQ 40 @ 0.25. ATRs
    # chosen so each stop clears its instrument's SCALP minimum and isn't rejected.
    rg = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                _vol(atr=5.0), "SCALP")
    rq = app._dynamic_stop_plan("Long", 20000.0, None, None, "MNQ",
                                _vol(atr=20.0), "SCALP")
    assert (rg["min_stop_ticks"], rg["tick_size"]) == (30, 0.1)
    assert (rq["min_stop_ticks"], rq["tick_size"]) == (40, 0.25)


def test_swing_specs_unchanged():
    # SWING parity guard (must stay byte-for-byte): MGC 50-tick floor, MNQ 40-tick
    # floor, base multiplier 1.5. ATRs chosen to clear each SWING minimum.
    rg = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                _vol(atr=10.0), "SWING")
    rq = app._dynamic_stop_plan("Long", 20000.0, None, None, "MNQ",
                                _vol(atr=30.0), "SWING")
    assert (rg["min_stop_ticks"], rg["multiplier"]) == (50, 1.5)
    assert (rq["min_stop_ticks"], rq["multiplier"]) == (40, 1.5)


def test_risk_dollars_uses_point_value():
    rg = app._dynamic_stop_plan("Long", 2000.0, None, None, "MGC",
                                _vol(atr=5.0), "SCALP")
    rq = app._dynamic_stop_plan("Long", 20000.0, None, None, "MNQ",
                                _vol(atr=20.0), "SCALP")
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
                                    volatility=_vol(atr=10.0, regime="NORMAL"),
                                    mode="SCALP")
    assert p["trade_plan"] is True
    assert p["atr_pts"] == 10.0
    assert p["atr_multiplier"] == 0.75
    assert p["min_stop_ticks"] == 30
    assert p["stop_distance_ticks"] >= 30
    assert p["risk_dollars_per_contract"] > 0
    assert p["nearest_demand"] == 1990.0
    assert float(p["stop_loss"]) < 2000.0          # below entry (Long)


def test_plan_success_short_stop_above_entry():
    p = app.build_strict_trade_plan("Short", "MNQ", 20000.0, 20010.0, 19990.0,
                                    volatility=_vol(atr=20.0, regime="NORMAL"),
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


def test_fixed_1to1_rr_swing_no_longer_vetoes():
    # Fixed-1:1 model: reward always mirrors risk, so the legacy SWING "<1:2 on TP2
    # -> no trade" veto no longer fires — a wide-stop SWING plan is still valid 1:1.
    def run():
        return app.build_strict_trade_plan(
            "Long", "MGC", 2000.0, 2010.0, 1990.0,
            volatility=_vol(atr=20.0, regime="NORMAL"), mode="SWING")
    p = _with_mode("SWING", run)
    assert p["trade_plan"] is True
    assert p["rr"] == "1:1"


def test_scalp_plan_is_fixed_1to1():
    # SCALP uses the same fixed-1:1 model, never gated on R:R.
    def run():
        return app.build_strict_trade_plan(
            "Long", "MGC", 2000.0, 2010.0, 1990.0,
            volatility=_vol(atr=10.0, regime="NORMAL"), mode="SCALP")
    p = _with_mode("SCALP", run)
    assert p["trade_plan"] is True
    assert p["rr"] == "1:1"


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
