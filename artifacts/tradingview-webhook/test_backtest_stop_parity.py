"""Parity tests: backtest `bt_stop_plan` vs live `_dynamic_stop_plan`.

The backtest stop helper is a COPY of the live ATR/structure stop, so the two must
stay in lock-step. These tests pin the cases the SCALP retune cares about:
  • the HARD minimum-stop rejection (too-tight -> no trade; no silent widening / floor)
  • a valid stop's snapped distance, multiplier, and min-tick metadata.

Only NORMAL-regime cases are compared directly: live keys the HIGH multiplier off
HIGH_CAUTION/HIGH_BLOCK while the backtest keys it off its own "VOLATILE" regime
label, so the elevated branch is intentionally out of scope for a direct equality.

Runnable two ways:
  • pytest test_backtest_stop_parity.py
  • python3 test_backtest_stop_parity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
import backtest_engine as bt  # noqa: E402


def _vol(atr, regime="NORMAL"):
    return {"status": "ok", "atr_pts": atr, "regime": regime,
            "label": "Normal", "ratio": 1.0}


# (ticker, entry, atr, mode) — ATRs chosen to clear each mode/instrument minimum.
_VALID_CASES = [
    ("MGC", 2000.0, 5.0, "SCALP"),
    ("MNQ", 20000.0, 20.0, "SCALP"),
    ("MGC", 2000.0, 10.0, "SWING"),
    ("MNQ", 20000.0, 30.0, "SWING"),
]


def test_valid_stop_parity_no_zone():
    for ticker, entry, atr, mode in _VALID_CASES:
        live = app._dynamic_stop_plan("Long", entry, None, None, ticker,
                                      _vol(atr), mode)
        b = bt.bt_stop_plan("Long", entry, None, None, bt.BT_SPECS[ticker],
                            atr, mode, "NORMAL")
        ctx = (ticker, mode)
        assert live["ok"] is True and b is not None, ctx
        assert b["multiplier"] == live["multiplier"], ctx
        assert b["stop_ticks"] == live["stop_distance_ticks"], ctx
        assert b["min_stop_ticks"] == live["min_stop_ticks"], ctx
        assert round(b["risk_points"], 4) == round(live["risk_points"], 4), ctx
        assert round(b["stop"], 4) == round(live["final_stop"], 4), ctx


def test_too_tight_rejected_parity():
    # Tiny ATR: both live and backtest must REJECT (no silent widening / tick floor).
    for ticker, entry in (("MGC", 2000.0), ("MNQ", 20000.0)):
        live = app._dynamic_stop_plan("Long", entry, None, None, ticker,
                                      _vol(0.2), "SCALP")
        b = bt.bt_stop_plan("Long", entry, None, None, bt.BT_SPECS[ticker],
                            0.2, "SCALP", "NORMAL")
        assert live["ok"] is False, ticker
        assert b is None, ticker


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
