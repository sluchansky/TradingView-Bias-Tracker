"""
Tests for Order Flow Engine V1.

Coverage:
  • Per-bar metric helpers (bar_delta, delta_ratio, delta_accel, cvd_slope,
    cvd_divergence, absorption)
  • Composite scorer and state classification
  • Reversal sequence detector
  • compute_order_flow() main entrypoint (flag ON/OFF, pre-V1 bars, happy path)
  • Constraint: flag OFF → compute_order_flow returns available=False, reason=flag_off
  • Constraint: no existing golden should change (tested by smoke at the end)

Run: python -m pytest tests/test_order_flow_v1.py -v
"""

import importlib
import sys
import types
import pytest

# ── Module under test ─────────────────────────────────────────────────────────
import order_flow_engine as ofe

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _bar(close=100.0, open_=100.0, high=101.0, low=99.0,
         volume=100, buy=0, sell=0, cvd_snap=None):
    """Build a minimal bar dict with optional OF fields."""
    b = {
        "ts":    1_700_000_000,
        "open":  open_,
        "high":  high,
        "low":   low,
        "close": close,
        "volume": volume,
    }
    if buy is not None or sell is not None:
        b["buy_volume"]  = buy  if buy  is not None else 0
        b["sell_volume"] = sell if sell is not None else 0
    if cvd_snap is not None:
        b["cvd_snapshot"] = cvd_snap
    return b


def _pre_v1_bar(close=100.0, volume=100):
    """Bar dict without buy/sell fields (pre-V1 legacy bar)."""
    return {"ts": 1_700_000_000, "open": close, "high": close+1,
            "low": close-1, "close": close, "volume": volume}


# ── Per-bar helpers ───────────────────────────────────────────────────────────

class TestComputeBarDelta:
    def test_buy_heavy(self):
        b = _bar(buy=80, sell=20)
        assert ofe.compute_bar_delta(b) == 60

    def test_sell_heavy(self):
        b = _bar(buy=10, sell=90)
        assert ofe.compute_bar_delta(b) == -80

    def test_balanced(self):
        b = _bar(buy=50, sell=50)
        assert ofe.compute_bar_delta(b) == 0

    def test_no_fields_returns_none(self):
        b = _pre_v1_bar()
        assert ofe.compute_bar_delta(b) is None

    def test_partial_fields_treated_as_zero(self):
        b = {"close": 100, "volume": 50, "buy_volume": 30}
        assert ofe.compute_bar_delta(b) == 30   # sell defaults to 0


class TestComputeDeltaRatio:
    def test_all_buys(self):
        b = _bar(buy=100, sell=0, volume=100)
        assert ofe.compute_delta_ratio(b) == pytest.approx(1.0, abs=1e-4)

    def test_all_sells(self):
        b = _bar(buy=0, sell=100, volume=100)
        assert ofe.compute_delta_ratio(b) == pytest.approx(-1.0, abs=1e-4)

    def test_neutral(self):
        b = _bar(buy=50, sell=50, volume=100)
        assert ofe.compute_delta_ratio(b) == pytest.approx(0.0, abs=1e-4)

    def test_zero_volume_returns_none(self):
        b = _bar(buy=0, sell=0, volume=0)
        assert ofe.compute_delta_ratio(b) is None

    def test_pre_v1_returns_none(self):
        assert ofe.compute_delta_ratio(_pre_v1_bar()) is None


class TestComputeDeltaAcceleration:
    def test_increasing_buy_pressure(self):
        bars = [_bar(buy=30, sell=70), _bar(buy=80, sell=20)]
        # d_now = 60, d_prev = -40, accel = 100
        assert ofe.compute_delta_acceleration(bars) == 100

    def test_decreasing_buy_pressure(self):
        bars = [_bar(buy=80, sell=20), _bar(buy=30, sell=70)]
        assert ofe.compute_delta_acceleration(bars) == -100

    def test_single_bar_returns_none(self):
        assert ofe.compute_delta_acceleration([_bar(buy=50, sell=50)]) is None

    def test_pre_v1_bars_return_none(self):
        bars = [_pre_v1_bar(), _pre_v1_bar()]
        assert ofe.compute_delta_acceleration(bars) is None


# ── Series helpers ────────────────────────────────────────────────────────────

class TestComputeCvdSlope:
    def test_rising_cvd(self):
        bars = [_bar(cvd_snap=100), _bar(cvd_snap=120), _bar(cvd_snap=140)]
        slope = ofe.compute_cvd_slope(bars, n=2)
        assert slope == pytest.approx(40.0)

    def test_falling_cvd(self):
        bars = [_bar(cvd_snap=200), _bar(cvd_snap=150)]
        assert ofe.compute_cvd_slope(bars, n=5) == pytest.approx(-50.0)

    def test_no_cvd_snap_returns_none(self):
        bars = [_bar(), _bar()]   # no cvd_snapshot key
        assert ofe.compute_cvd_slope(bars) is None

    def test_single_valid_bar_returns_none(self):
        bars = [_bar(cvd_snap=100)]
        assert ofe.compute_cvd_slope(bars) is None


class TestComputeCvdDivergence:
    def test_bullish_divergence(self):
        # Price fell but CVD rose → bullish
        bars = [_bar(close=100, cvd_snap=50), _bar(close=95, cvd_snap=60)]
        assert ofe.compute_cvd_divergence(bars, n=1) == "BULLISH"

    def test_bearish_divergence(self):
        # Price rose but CVD fell → bearish
        bars = [_bar(close=100, cvd_snap=60), _bar(close=105, cvd_snap=50)]
        assert ofe.compute_cvd_divergence(bars, n=1) == "BEARISH"

    def test_aligned_up_returns_none(self):
        bars = [_bar(close=100, cvd_snap=50), _bar(close=105, cvd_snap=60)]
        assert ofe.compute_cvd_divergence(bars, n=1) is None

    def test_insufficient_bars_returns_none(self):
        assert ofe.compute_cvd_divergence([_bar(cvd_snap=50)], n=3) is None

    def test_missing_cvd_snap_returns_none(self):
        bars = [_bar(close=100), _bar(close=105)]
        assert ofe.compute_cvd_divergence(bars, n=1) is None


class TestComputeAbsorption:
    def test_sell_absorption_strong(self):
        # Heavy sellers (ratio=-0.80) but price closed higher
        b = _bar(open_=100, close=101, buy=10, sell=90, volume=100)
        side, strength = ofe.compute_absorption([b])
        assert side == "SELLERS_ABSORBED"
        assert strength == "STRONG"

    def test_sell_absorption_moderate(self):
        b = _bar(open_=100, close=100.5, buy=25, sell=60, volume=85)
        # ratio ≈ -35/85 = -0.41 — below threshold → no absorption
        # Let me build a proper moderate case
        b = _bar(open_=100, close=100, buy=20, sell=60, volume=80)
        # ratio = -40/80 = -0.50 → >= min threshold, < strong threshold → MODERATE
        side, strength = ofe.compute_absorption([b])
        assert side == "SELLERS_ABSORBED"
        assert strength == "MODERATE"

    def test_buy_absorption(self):
        # Heavy buyers but price closed flat/lower
        b = _bar(open_=100, close=99, buy=80, sell=10, volume=90)
        side, strength = ofe.compute_absorption([b])
        assert side == "BUYERS_ABSORBED"
        assert strength == "STRONG"

    def test_no_absorption_when_directional(self):
        # Buyers dominate AND price rises — no absorption (price followed delta)
        b = _bar(open_=100, close=102, buy=80, sell=10, volume=90)
        side, strength = ofe.compute_absorption([b])
        assert side is None
        assert strength is None

    def test_empty_bars_returns_none(self):
        assert ofe.compute_absorption([]) == (None, None)

    def test_low_volume_returns_none(self):
        b = _bar(buy=5, sell=1, volume=6)
        assert ofe.compute_absorption([b]) == (None, None)


# ── Composite scorer ──────────────────────────────────────────────────────────

class TestComputeOrderFlowScore:
    def _score(self, **kw):
        defaults = dict(
            cvd_state=None, cvd_slope=None, bar_delta=None,
            delta_ratio=None, delta_accel=None,
            absorption_side=None, cvd_divergence=None,
        )
        defaults.update(kw)
        return ofe.compute_order_flow_score(**defaults)

    def test_neutral_all_none(self):
        assert self._score() == 50

    def test_bullish_cvd_adds_score(self):
        s = self._score(cvd_state="bullish")
        assert s > 50

    def test_bearish_cvd_subtracts_score(self):
        s = self._score(cvd_state="bearish")
        assert s < 50

    def test_full_bullish_stack(self):
        s = self._score(
            cvd_state="bullish", cvd_slope=100.0,
            bar_delta=500, delta_ratio=0.60, delta_accel=100,
            absorption_side="SELLERS_ABSORBED", cvd_divergence="BULLISH",
        )
        assert s >= 75   # STRONG_BULLISH territory

    def test_full_bearish_stack(self):
        s = self._score(
            cvd_state="bearish", cvd_slope=-100.0,
            bar_delta=-500, delta_ratio=-0.60, delta_accel=-100,
            absorption_side="BUYERS_ABSORBED", cvd_divergence="BEARISH",
        )
        assert s <= 25   # STRONG_BEARISH territory

    def test_score_clamped_0_to_100(self):
        s_max = self._score(
            cvd_state="bullish", cvd_slope=999,
            bar_delta=9999, delta_ratio=1.0, delta_accel=9999,
            absorption_side="SELLERS_ABSORBED", cvd_divergence="BULLISH",
        )
        s_min = self._score(
            cvd_state="bearish", cvd_slope=-999,
            bar_delta=-9999, delta_ratio=-1.0, delta_accel=-9999,
            absorption_side="BUYERS_ABSORBED", cvd_divergence="BEARISH",
        )
        assert 0 <= s_max <= 100
        assert 0 <= s_min <= 100

    def test_deceleration_penalised(self):
        # Accel going negative while bar_delta is positive = decelerating buy
        s_decel = self._score(bar_delta=500, delta_ratio=0.50, delta_accel=-200)
        s_clean = self._score(bar_delta=500, delta_ratio=0.50, delta_accel=0)
        assert s_decel < s_clean


class TestScoreToState:
    def test_boundaries(self):
        from order_flow_engine import _score_to_state
        assert _score_to_state(100) == "STRONG_BULLISH"
        assert _score_to_state(75)  == "STRONG_BULLISH"
        assert _score_to_state(74)  == "BULLISH"
        assert _score_to_state(58)  == "BULLISH"
        assert _score_to_state(57)  == "NEUTRAL"
        assert _score_to_state(43)  == "NEUTRAL"
        assert _score_to_state(42)  == "BEARISH"
        assert _score_to_state(26)  == "BEARISH"
        assert _score_to_state(25)  == "STRONG_BEARISH"
        assert _score_to_state(0)   == "STRONG_BEARISH"


# ── Reversal sequence detector ────────────────────────────────────────────────

class TestDetectReversalSequence:
    def _make_sequence(self, wick_body_ratio=2.0, vol_mult=1.5, avg_vol=100,
                       absorb_ratio=-0.60, delta_b3=200, b4_move=+1.0):
        """Build a minimal 8-bar sequence ending with a bullish reversal."""
        # 4 prior bars for average volume
        prior = [_bar(volume=avg_vol, buy=40, sell=60,
                      open_=100, close=100, cvd_snap=0) for _ in range(4)]
        sweep_vol = int(avg_vol * vol_mult)
        # b1: sweep bar (large wick)
        b1 = _bar(open_=100, close=99, high=104, low=96,
                  buy=30, sell=70, volume=sweep_vol)  # body=1, wick=8 → ratio=8
        # b2: absorption (large sell delta but price held)
        b2 = _bar(open_=99, close=99.5,
                  buy=20, sell=80, volume=100)
        # b3: delta reversal (buy side dominates)
        b3 = _bar(open_=99.5, close=100,
                  buy=int(100*(0.5+abs(absorb_ratio)/2)),
                  sell=int(100*(0.5-abs(absorb_ratio)/2)),
                  volume=100)
        # Ensure b3 has positive delta
        b3["buy_volume"]  = 70
        b3["sell_volume"] = 30
        # b4: confirmation (price closes up)
        b4 = _bar(open_=100, close=100+b4_move,
                  buy=60, sell=40, volume=100)
        return prior + [b1, b2, b3, b4]

    def test_valid_bullish_reversal_detected(self):
        bars = self._make_sequence()
        assert ofe.detect_reversal_sequence(bars) is True

    def test_insufficient_bars_returns_false(self):
        assert ofe.detect_reversal_sequence([]) is False
        assert ofe.detect_reversal_sequence([_bar(buy=60, sell=40)] * 3) is False

    def test_no_wick_sweep_fails(self):
        # Doji candle — body ≈ range → wick ratio < threshold
        bars = self._make_sequence()
        bars[-4] = _bar(open_=100, close=100, high=100.3, low=99.7,
                        buy=30, sell=70, volume=200)  # tiny wick
        assert ofe.detect_reversal_sequence(bars) is False

    def test_no_delta_flip_fails(self):
        bars = self._make_sequence()
        # Both b2 and b3 have negative delta — no flip
        bars[-2]["buy_volume"]  = 30
        bars[-2]["sell_volume"] = 70
        assert ofe.detect_reversal_sequence(bars) is False

    def test_pre_v1_bars_return_false(self):
        bars = [_pre_v1_bar()] * 8
        assert ofe.detect_reversal_sequence(bars) is False

    def test_exception_safety(self):
        # Corrupt bar should not raise
        assert ofe.detect_reversal_sequence([{"broken": True}] * 8) is False


# ── compute_order_flow main entrypoint ────────────────────────────────────────

class TestComputeOrderFlow:
    def setup_method(self):
        # Ensure flag ON for most tests
        import order_flow_engine as _m
        self._orig_flag = _m.ORDER_FLOW_V1_ENABLED
        _m.ORDER_FLOW_V1_ENABLED = True

    def teardown_method(self):
        import order_flow_engine as _m
        _m.ORDER_FLOW_V1_ENABLED = self._orig_flag

    def _bars(self, n=8):
        bars = []
        cvd = 0.0
        for i in range(n):
            buy  = 60 + i * 2
            sell = 40 - i
            cvd += buy - sell
            bars.append(_bar(
                open_=100 + i * 0.5, close=100 + (i+1) * 0.5,
                high=101 + i * 0.5, low=99 + i * 0.5,
                volume=buy + sell, buy=buy, sell=sell,
                cvd_snap=cvd,
            ))
        return bars

    # ── Flag OFF ──────────────────────────────────────────────────────────────

    def test_flag_off_returns_stub(self):
        import order_flow_engine as _m
        _m.ORDER_FLOW_V1_ENABLED = False
        result = _m.compute_order_flow("MNQ", self._bars())
        assert result["available"] is False
        assert result["reason"] == "flag_off"

    def test_flag_off_does_not_raise(self):
        import order_flow_engine as _m
        _m.ORDER_FLOW_V1_ENABLED = False
        # Even with None inputs
        result = _m.compute_order_flow("MNQ", None)
        assert result["available"] is False

    # ── Pre-V1 bars ───────────────────────────────────────────────────────────

    def test_pre_v1_bars_returns_stub(self):
        bars = [_pre_v1_bar() for _ in range(8)]
        result = ofe.compute_order_flow("MNQ", bars)
        assert result["available"] is False
        assert result["reason"] == "bars_pre_v1"
        assert result["order_flow_score"] is None
        assert result["order_flow_state"] is None

    # ── No bars ───────────────────────────────────────────────────────────────

    def test_no_bars_returns_stub(self):
        result = ofe.compute_order_flow("MNQ", [])
        assert result["available"] is False
        assert result["reason"] == "no_bars"

    def test_none_bars_deque_returns_stub(self):
        result = ofe.compute_order_flow("MNQ", None)
        assert result["available"] is False

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_happy_path_available_true(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        assert result["available"] is True

    def test_happy_path_all_keys_present(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        expected_keys = [
            "available", "instrument", "bar_delta", "delta_ratio",
            "delta_acceleration", "ask_volume", "bid_volume", "book_imbalance",
            "cvd", "cvd_slope", "cvd_divergence", "absorption_side",
            "absorption_strength", "order_flow_score", "order_flow_state",
            "order_flow_reversal_confirmed",
        ]
        for k in expected_keys:
            assert k in result, f"Missing key: {k}"

    def test_book_imbalance_always_none(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        assert result["book_imbalance"] is None

    def test_score_in_range(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        score = result["order_flow_score"]
        assert isinstance(score, int)
        assert 0 <= score <= 100

    def test_state_is_valid_string(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        assert result["order_flow_state"] in {
            "STRONG_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONG_BEARISH"
        }

    def test_reversal_is_bool(self):
        result = ofe.compute_order_flow("MNQ", self._bars())
        assert isinstance(result["order_flow_reversal_confirmed"], bool)

    def test_cvd_record_used_when_provided(self):
        cvd = {"state": "bullish", "value": 1234.5, "direction": "up"}
        result = ofe.compute_order_flow("MNQ", self._bars(), cvd)
        assert result["cvd"] == 1234.5

    def test_cvd_record_none_no_crash(self):
        result = ofe.compute_order_flow("MNQ", self._bars(), None)
        assert result["available"] is True

    def test_instrument_name_in_result(self):
        result = ofe.compute_order_flow("MGC", self._bars())
        assert result["instrument"] == "MGC"

    def test_deque_input_accepted(self):
        from collections import deque
        bars_deque = deque(self._bars(), maxlen=200)
        result = ofe.compute_order_flow("MNQ", bars_deque)
        assert result["available"] is True

    # ── Bullish pressure scenario ─────────────────────────────────────────────

    def test_heavy_buy_pressure_scores_above_50(self):
        bars = [_bar(buy=80, sell=20, volume=100, cvd_snap=float(i*60))
                for i in range(8)]
        cvd = {"state": "bullish", "value": 400.0}
        result = ofe.compute_order_flow("MNQ", bars, cvd)
        assert result["order_flow_score"] > 50

    # ── Heavy sell pressure scenario ──────────────────────────────────────────

    def test_heavy_sell_pressure_scores_below_50(self):
        bars = [_bar(buy=20, sell=80, volume=100, cvd_snap=float(-i*60))
                for i in range(8)]
        cvd = {"state": "bearish", "value": -400.0}
        result = ofe.compute_order_flow("MNQ", bars, cvd)
        assert result["order_flow_score"] < 50

    # ── Exception safety ─────────────────────────────────────────────────────

    def test_corrupt_bars_fail_open(self):
        bars = [{"broken": True, "buy_volume": "not_a_number"}]
        result = ofe.compute_order_flow("MNQ", bars)
        # Should either return stub or available=True with graceful None values
        assert isinstance(result, dict)
        assert "available" in result


# ── _bars_have_of_fields ──────────────────────────────────────────────────────

class TestBarsHaveOfFields:
    def test_empty_list_returns_false(self):
        assert ofe._bars_have_of_fields([]) is False

    def test_pre_v1_bar_returns_false(self):
        assert ofe._bars_have_of_fields([_pre_v1_bar()]) is False

    def test_v1_bar_returns_true(self):
        assert ofe._bars_have_of_fields([_bar(buy=50, sell=50)]) is True


# ── Regression: flag OFF is byte-identical to no order_flow key ───────────────

class TestFlagOffRegressionSmoke:
    """
    When ORDER_FLOW_V1_ENABLED=0 (the default), compute_order_flow() MUST
    return {available: False, reason: flag_off} and nothing else must change.
    Specifically, result["order_flow"] is absent from full_analysis when flag OFF.
    This is enforced by the flag check in app.py full_analysis itself.
    We verify the engine contract here (flag OFF → stub, not an exception).
    """

    def test_flag_off_engine_contract(self):
        import order_flow_engine as _m
        orig = _m.ORDER_FLOW_V1_ENABLED
        _m.ORDER_FLOW_V1_ENABLED = False
        try:
            r = _m.compute_order_flow("MNQ", [_bar(buy=60, sell=40)] * 10)
            assert r == {"available": False, "reason": "flag_off"}
        finally:
            _m.ORDER_FLOW_V1_ENABLED = orig

    def test_flag_off_order_flow_score_is_absent(self):
        import order_flow_engine as _m
        orig = _m.ORDER_FLOW_V1_ENABLED
        _m.ORDER_FLOW_V1_ENABLED = False
        try:
            r = _m.compute_order_flow("MNQ", [_bar(buy=60, sell=40)] * 10)
            assert "order_flow_score" not in r
        finally:
            _m.ORDER_FLOW_V1_ENABLED = orig


class TestMbp1BookImbalance:
    @staticmethod
    def _book(bid_size=80, ask_size=20, bid_price=100.0, ask_price=100.25):
        return {
            "available": True,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "bid_price": bid_price,
            "ask_price": ask_price,
        }

    def test_bid_and_ask_heavy_books_are_symmetric(self):
        assert ofe.compute_book_imbalance(self._book()) == pytest.approx(0.6)
        assert ofe.compute_book_imbalance(self._book(20, 80)) == pytest.approx(-0.6)

    def test_missing_or_invalid_book_is_a_noop(self):
        assert ofe.compute_book_imbalance(None) is None
        assert ofe.compute_book_imbalance({"available": False}) is None
        assert ofe.compute_book_imbalance(self._book(bid_size=0)) is None
        assert ofe.compute_book_imbalance(self._book(ask_price=99.0)) is None

    def test_book_pressure_updates_only_the_existing_composite(self):
        common = dict(
            cvd_state=None, cvd_slope=None, bar_delta=None, delta_ratio=None,
            delta_accel=None, absorption_side=None, cvd_divergence=None,
        )
        assert ofe.compute_order_flow_score(**common, book_imbalance=0.60) == (
            50 + ofe._WEIGHTS["book_imbalance"]
        )
        assert ofe.compute_order_flow_score(**common, book_imbalance=-0.60) == (
            50 - ofe._WEIGHTS["book_imbalance"]
        )

    def test_fresh_mbp1_snapshot_populates_order_flow_result(self):
        original_flag = ofe.ORDER_FLOW_V1_ENABLED
        ofe.ORDER_FLOW_V1_ENABLED = True
        try:
            bars = [_bar(buy=60, sell=40, volume=100, cvd_snap=float(i)) for i in range(8)]
            result = ofe.compute_order_flow("MNQ", bars, book_snapshot=self._book())
            assert result["book_imbalance"] == pytest.approx(0.6)
        finally:
            ofe.ORDER_FLOW_V1_ENABLED = original_flag
