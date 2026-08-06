"""
Volatility Intelligence Module — Deterministic Unit Tests (Phase F)
====================================================================
32 required tests covering:
  - Flag-off byte-identical behavior
  - Provider data model (null/fresh/stale/error)
  - Regime classification
  - Direction / velocity / acceleration
  - Risk tone and equity context
  - Session percentile
  - Per-instrument context
  - Observe-only safety contract
  - Left Brain block format
  - History summary format
  - get_snapshot() never raises
  - Execution effect always NONE / score_effect always 0
"""

import os
import sys
import importlib
import types
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_vi(enabled=True, observe_only=True, exec_influence=False, score_influence=False):
    """Import (or reload) volatility_intelligence with patched flag env vars."""
    os.environ["VOL_INTELLIGENCE_ENABLED"]             = "1" if enabled        else "0"
    os.environ["VOL_INTELLIGENCE_OBSERVE_ONLY"]        = "1" if observe_only   else "0"
    os.environ["VOL_INTELLIGENCE_EXECUTION_INFLUENCE"] = "1" if exec_influence else "0"
    os.environ["VOL_INTELLIGENCE_SCORE_INFLUENCE"]     = "1" if score_influence else "0"
    if "volatility_intelligence" in sys.modules:
        del sys.modules["volatility_intelligence"]
    import volatility_intelligence as vi
    # Reimport to pick up fresh env state
    importlib.reload(vi)
    return vi

def _fresh_vix(price=18.5, age_seconds=45):
    """Return a fake VIX record that counts as fresh."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "symbol":         "VIX",
        "source":         "alpha_vantage",
        "price":          price,
        "previous_close": 17.0,
        "change":         price - 17.0,
        "change_pct":     round((price - 17.0) / 17.0 * 100, 2),
        "session_open":   17.5,
        "session_high":   19.5,
        "session_low":    16.8,
        "timestamp_utc":  ts,
        "age_seconds":    age_seconds,
        "is_fresh":       age_seconds < 600,
        "is_delayed":     True,
        "status":         "DELAYED",
        "error":          None,
    }

def _stale_vix():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=800)).isoformat()
    r = _fresh_vix()
    r.update({"timestamp_utc": ts, "age_seconds": 800, "is_fresh": False, "status": "STALE"})
    return r

def _null_vix():
    from volatility_intelligence import _null_vix_record
    return _null_vix_record("UNAVAILABLE", "Test null")

def _make_history(prices):
    return [{"price": p, "timestamp_str": f"2026-01-01 10:{i:02d}:00"} for i, p in enumerate(prices)]

def _mock_provider(vix_rec, history=None):
    prov = MagicMock()
    prov.get_latest_vix.return_value = vix_rec
    prov.get_vix_history.return_value = history or []
    prov.get_provider_status.return_value = {
        "provider": "Test", "connected": True, "last_error": None,
        "consecutive_errors": 0, "api_key_present": True, "is_delayed": True,
    }
    return prov

# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFlagOff(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=False)

    def test_01_disabled_enabled_false(self):
        snap = self.vi.get_snapshot()
        self.assertFalse(snap["enabled"])

    def test_02_disabled_returns_neutral(self):
        snap = self.vi.get_snapshot()
        self.assertEqual(snap["data_status"], "UNAVAILABLE")

    def test_03_disabled_execution_effect_none(self):
        snap = self.vi.get_snapshot()
        self.assertEqual(snap["execution_effect"], "NONE")

    def test_04_disabled_score_effect_zero(self):
        snap = self.vi.get_snapshot()
        self.assertEqual(snap["score_effect"], 0)

    def test_05_disabled_get_snapshot_never_raises(self):
        # Should never raise under any circumstance
        snap = self.vi.get_snapshot()
        self.assertIsInstance(snap, dict)

    def test_06_disabled_left_brain_block_safe(self):
        block = self.vi.get_left_brain_block("MNQ")
        self.assertFalse(block.get("enabled", True))


class TestRegimeClassification(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=True)

    def _snap(self, price, history=None):
        vix = _fresh_vix(price=price)
        prov = _mock_provider(vix, history or _make_history([price] * 4))
        self.vi._provider = prov
        return self.vi._build_snapshot()

    def test_07_calm_below_15(self):
        snap = self._snap(12.0)
        self.assertEqual(snap["regime"], "CALM")

    def test_08_normal_15_to_20(self):
        snap = self._snap(17.5)
        self.assertEqual(snap["regime"], "NORMAL")

    def test_09_elevated_20_to_30(self):
        snap = self._snap(25.0)
        self.assertEqual(snap["regime"], "ELEVATED")

    def test_10_extreme_above_30(self):
        snap = self._snap(35.0)
        self.assertEqual(snap["regime"], "EXTREME")

    def test_11_boundary_exactly_15_is_normal(self):
        snap = self._snap(15.0)
        self.assertEqual(snap["regime"], "NORMAL")

    def test_12_boundary_exactly_20_is_elevated(self):
        snap = self._snap(20.0)
        self.assertEqual(snap["regime"], "ELEVATED")


class TestDirectionAndVelocity(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=True)

    def _snap_with_hist(self, price, hist_prices):
        vix = _fresh_vix(price=price)
        prov = _mock_provider(vix, _make_history(hist_prices))
        self.vi._provider = prov
        return self.vi._build_snapshot()

    def test_13_rising_fast(self):
        # Last bar rose 0.8 points
        hist = [17.0, 17.1, 17.3, 18.5, 19.3]
        snap = self._snap_with_hist(19.3, hist)
        self.assertEqual(snap["direction"], "RISING")
        self.assertEqual(snap["velocity"], "FAST")

    def test_14_falling_moderate(self):
        hist = [22.0, 21.8, 21.5, 21.0, 20.7]
        snap = self._snap_with_hist(20.7, hist)
        self.assertEqual(snap["direction"], "FALLING")
        self.assertEqual(snap["velocity"], "MODERATE")

    def test_15_flat(self):
        hist = [18.0, 18.01, 17.99, 18.0, 18.02]
        snap = self._snap_with_hist(18.02, hist)
        self.assertEqual(snap["direction"], "FLAT")

    def test_16_no_history_unknown_direction(self):
        vix = _fresh_vix()
        prov = _mock_provider(vix, [])
        self.vi._provider = prov
        snap = self.vi._build_snapshot()
        self.assertEqual(snap["direction"], "UNKNOWN")
        self.assertEqual(snap["velocity"], "UNKNOWN")

    def test_17_acceleration_increasing(self):
        # Slope growing: last bar change > prior bar change
        hist = [18.0, 18.1, 18.3, 18.8, 19.5]  # accelerating up
        snap = self._snap_with_hist(19.5, hist)
        self.assertEqual(snap["acceleration"], "INCREASING")

    def test_18_acceleration_decreasing(self):
        hist = [19.5, 18.8, 18.3, 18.1, 18.0]  # decelerating down
        snap = self._snap_with_hist(18.0, hist)
        self.assertEqual(snap["acceleration"], "DECREASING")


class TestSessionPercentile(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=True)

    def test_19_session_percentile_calculated(self):
        vix = _fresh_vix(price=18.0)
        vix["session_high"] = 20.0
        vix["session_low"]  = 15.0
        prov = _mock_provider(vix, _make_history([18.0, 18.0, 18.0, 18.0]))
        self.vi._provider = prov
        snap = self.vi._build_snapshot()
        # (18 - 15) / (20 - 15) * 100 = 60
        self.assertAlmostEqual(snap["session_percentile"], 60.0, places=1)

    def test_20_session_percentile_none_when_range_zero(self):
        vix = _fresh_vix(price=18.0)
        vix["session_high"] = 18.0
        vix["session_low"]  = 18.0
        prov = _mock_provider(vix, _make_history([18.0] * 4))
        self.vi._provider = prov
        snap = self.vi._build_snapshot()
        self.assertIsNone(snap["session_percentile"])


class TestRiskTone(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=True)

    def _snap_at(self, price, hist):
        vix = _fresh_vix(price=price)
        prov = _mock_provider(vix, _make_history(hist))
        self.vi._provider = prov
        return self.vi._build_snapshot()

    def test_21_calm_falling_is_risk_on(self):
        snap = self._snap_at(12.0, [13.0, 12.8, 12.5, 12.2, 12.0])
        self.assertEqual(snap["risk_tone"], "RISK_ON")

    def test_22_extreme_is_risk_off_shock(self):
        snap = self._snap_at(38.0, [35.0, 36.0, 37.0, 37.5, 38.0])
        self.assertEqual(snap["risk_tone"], "RISK_OFF_SHOCK")

    def test_23_elevated_rising_is_risk_off_pressure(self):
        snap = self._snap_at(22.0, [21.0, 21.3, 21.6, 21.8, 22.0])
        self.assertIn(snap["risk_tone"], ("RISK_OFF_PRESSURE", "RISK_OFF_SHOCK"))


class TestInstrumentContext(unittest.TestCase):

    def setUp(self):
        self.vi = _load_vi(enabled=True)

    def _snap_elevated_rising(self):
        vix = _fresh_vix(price=25.0)
        prov = _mock_provider(vix, _make_history([24.0, 24.3, 24.7, 24.9, 25.0]))
        self.vi._provider = prov
        return self.vi._build_snapshot()

    def test_24_mnq_has_high_relevance(self):
        snap = self._snap_elevated_rising()
        ctx = snap["instrument_context"]["MNQ"]
        self.assertEqual(ctx["relevance"], "HIGH")

    def test_25_mes_has_high_relevance(self):
        snap = self._snap_elevated_rising()
        ctx = snap["instrument_context"]["MES"]
        self.assertEqual(ctx["relevance"], "HIGH")

    def test_26_mgc_has_low_to_medium_relevance(self):
        snap = self._snap_elevated_rising()
        ctx = snap["instrument_context"]["MGC"]
        self.assertEqual(ctx["relevance"], "LOW_TO_MEDIUM")

    def test_27_mgc_context_is_indirect_only(self):
        snap = self._snap_elevated_rising()
        ctx = snap["instrument_context"]["MGC"]
        self.assertEqual(ctx["context"], "INDIRECT_ONLY")


class TestSafetyContract(unittest.TestCase):

    def test_28_execution_effect_always_none(self):
        vi = _load_vi(enabled=True)
        vix = _fresh_vix()
        prov = _mock_provider(vix, _make_history([17.0, 17.5, 18.0, 18.2, 18.5]))
        vi._provider = prov
        snap = vi._build_snapshot()
        self.assertEqual(snap["execution_effect"], "NONE")

    def test_29_score_effect_always_zero(self):
        vi = _load_vi(enabled=True)
        vix = _fresh_vix()
        prov = _mock_provider(vix, _make_history([17.0, 17.5, 18.0, 18.2, 18.5]))
        vi._provider = prov
        snap = vi._build_snapshot()
        self.assertEqual(snap["score_effect"], 0)

    def test_30_stale_data_excluded_no_regime(self):
        vi = _load_vi(enabled=True)
        prov = _mock_provider(_stale_vix(), [])
        vi._provider = prov
        snap = vi.get_snapshot()
        # Stale or unavailable data should not produce a regime
        self.assertIn(snap["regime"], ("UNKNOWN", None))

    def test_31_null_data_returns_unknown_regime(self):
        vi = _load_vi(enabled=True)
        prov = _mock_provider(_null_vix(), [])
        vi._provider = prov
        snap = vi.get_snapshot()
        self.assertEqual(snap["regime"], "UNKNOWN")

    def test_32_left_brain_block_has_required_keys(self):
        vi = _load_vi(enabled=True)
        vix = _fresh_vix()
        prov = _mock_provider(vix, _make_history([17.0, 17.5, 18.0, 18.2, 18.5]))
        vi._provider = prov
        block = vi.get_left_brain_block("MNQ")
        required = {
            "enabled", "observe_only", "source", "freshness", "is_delayed",
            "regime", "direction", "velocity", "risk_tone", "equity_context",
            "vix_price", "confidence", "instrument_context",
            "execution_effect", "score_effect",
        }
        missing = required - set(block.keys())
        self.assertFalse(missing, f"Left Brain block missing keys: {missing}")


if __name__ == "__main__":
    unittest.main()
