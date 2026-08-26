"""Tests: Visual Brain multi-instrument extension (Task #189).

Covers:
  - _VB_SYMBOLS parsing from comma-separated env var
  - Per-instrument _LAST_OBSERVATION_BY_INST cache isolation
  - get_last_observation() returns correct per-instrument data
  - _schedule_next() creates per-instrument timers in _VB_TIMERS
  - start() creates staggered timers for each instrument (0s, 20s, 40s)
  - Flag-OFF: start() returns without spawning any timers → byte-identical
  - Cost counter is shared across instruments
  - _vb_tick(instrument) writes only to the correct instrument slot
  - All tests reset module state between runs (no cross-test contamination)
"""

from __future__ import annotations

import sys
import os
import threading
import types
import importlib
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers: load visual_brain with a patched environment
# ---------------------------------------------------------------------------

def _load_vb(env_overrides: dict | None = None, *, enabled: bool = False) -> types.ModuleType:
    """Import (or re-import) visual_brain with patched environment.

    Forces a fresh module load so module-level constants are re-evaluated
    from env each time.  Cleans up sys.modules after the test is done.
    """
    env = {
        "VISUAL_BRAIN_ENABLED": "true" if enabled else "false",
        "VISUAL_BRAIN_INTERVAL_SECONDS": "300",
        **(env_overrides or {}),
    }
    # Remove any previously cached module so constants re-evaluate
    sys.modules.pop("visual_brain", None)

    # Patch openai import (not installed in test env)
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()
    sys.modules["openai"] = fake_openai

    # Patch playwright (not installed in test env)
    for mod in ["playwright", "playwright.async_api", "playwright.sync_api"]:
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)

    with patch.dict(os.environ, env, clear=False):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "visual_brain",
            os.path.join(os.path.dirname(__file__), "..", "visual_brain.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["visual_brain"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _cleanup_vb() -> None:
    sys.modules.pop("visual_brain", None)


# ---------------------------------------------------------------------------
# 1. _VB_SYMBOLS parsing
# ---------------------------------------------------------------------------

class TestVBSymbolsParsing(unittest.TestCase):
    def tearDown(self) -> None:
        _cleanup_vb()

    def test_default_two_instruments(self):
        """Default config (no override) must produce MNQ and MGC loops only."""
        vb = _load_vb()
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC"])

    def test_single_custom_symbol(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MGC"})
        self.assertEqual(vb._VB_SYMBOLS, ["MGC"])

    def test_default_preserves_string_compat(self):
        """VISUAL_BRAIN_SYMBOL remains a comma-separated string for compatibility."""
        vb = _load_vb()
        for t in ("MNQ", "MGC"):
            self.assertIn(t, vb.VISUAL_BRAIN_SYMBOL)

    def test_default_interval_is_five_minutes(self):
        """Default observer cadence is five minutes."""
        vb = _load_vb()
        self.assertEqual(vb.VISUAL_BRAIN_INTERVAL, 300)

    def test_two_symbols_comma_separated(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC"})
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC"])

    def test_four_symbols_with_spaces(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ, MGC, MES, MYM"})
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC", "MES", "MYM"])

    def test_symbols_uppercased(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "mnq,mgc"})
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC"])

    def test_trailing_comma_ignored(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC,"})
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC"])

    def test_original_visual_brain_symbol_preserved(self):
        """VISUAL_BRAIN_SYMBOL kept as string for back-compat."""
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC"})
        self.assertIsInstance(vb.VISUAL_BRAIN_SYMBOL, str)
        self.assertIn("MNQ", vb.VISUAL_BRAIN_SYMBOL)


# ---------------------------------------------------------------------------
# 2. Per-instrument cache isolation
# ---------------------------------------------------------------------------

class TestPerInstrumentCache(unittest.TestCase):
    def setUp(self) -> None:
        self.vb = _load_vb()

    def tearDown(self) -> None:
        _cleanup_vb()

    def test_cache_starts_empty(self):
        self.assertEqual(self.vb._LAST_OBSERVATION_BY_INST, {})

    def test_write_different_instruments_isolated(self):
        vb = self.vb
        obs_mnq = {"instrument": "MNQ", "bias": "BULLISH", "action": "LONG_WATCH", "confidence": 80}
        obs_mgc = {"instrument": "MGC", "bias": "BEARISH", "action": "SHORT_WATCH", "confidence": 70}
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = obs_mnq
            vb._LAST_OBSERVATION_BY_INST["MGC"] = obs_mgc
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MNQ"]["bias"], "BULLISH")
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MGC"]["bias"], "BEARISH")

    def test_overwrite_one_does_not_affect_other(self):
        vb = self.vb
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = {"bias": "BULLISH"}
            vb._LAST_OBSERVATION_BY_INST["MGC"] = {"bias": "BEARISH"}
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = {"bias": "NEUTRAL"}
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MGC"]["bias"], "BEARISH")


# ---------------------------------------------------------------------------
# 3. get_last_observation — per-instrument reads
# ---------------------------------------------------------------------------

class TestGetLastObservation(unittest.TestCase):
    def setUp(self) -> None:
        self.vb = _load_vb()
        # Disable DB fallback
        self.vb.VB_DB_READY = False

    def tearDown(self) -> None:
        _cleanup_vb()

    def test_returns_none_when_cache_empty(self):
        self.assertIsNone(self.vb.get_last_observation("MNQ"))

    def test_returns_cached_mnq(self):
        vb = self.vb
        obs = {"instrument": "MNQ", "bias": "BULLISH", "confidence": 90}
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = obs
        result = vb.get_last_observation("MNQ")
        self.assertIsNotNone(result)
        self.assertEqual(result["bias"], "BULLISH")

    def test_returns_none_for_missing_instrument(self):
        vb = self.vb
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = {"bias": "BULLISH"}
        self.assertIsNone(vb.get_last_observation("MGC"))

    def test_returns_copy_not_reference(self):
        """Mutation of returned dict must not affect the cache."""
        vb = self.vb
        obs = {"bias": "BULLISH", "confidence": 80}
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = obs
        result = vb.get_last_observation("MNQ")
        result["bias"] = "BEARISH"
        # Cache should be unchanged
        with vb._VB_LOCK:
            self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MNQ"]["bias"], "BULLISH")

    def test_separate_instruments_separate_data(self):
        vb = self.vb
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = {"bias": "BULLISH"}
            vb._LAST_OBSERVATION_BY_INST["MGC"] = {"bias": "BEARISH"}
        self.assertEqual(vb.get_last_observation("MNQ")["bias"], "BULLISH")
        self.assertEqual(vb.get_last_observation("MGC")["bias"], "BEARISH")

    def test_default_instrument_is_mnq(self):
        vb = self.vb
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MNQ"] = {"bias": "BULLISH"}
        result = vb.get_last_observation()  # no arg → default "MNQ"
        self.assertEqual(result["bias"], "BULLISH")


# ---------------------------------------------------------------------------
# 4. _schedule_next — per-instrument timer creation
# ---------------------------------------------------------------------------

class TestScheduleNext(unittest.TestCase):
    def setUp(self) -> None:
        self.vb = _load_vb(enabled=True)

    def tearDown(self) -> None:
        # Cancel any spawned timers
        for t in self.vb._VB_TIMERS.values():
            try:
                t.cancel()
            except Exception:
                pass
        _cleanup_vb()

    def test_schedule_next_disabled_is_noop(self):
        vb = _load_vb(enabled=False)
        vb._schedule_next("MNQ")
        self.assertEqual(vb._VB_TIMERS, {})
        _cleanup_vb()

    def test_schedule_next_creates_timer_for_instrument(self):
        vb = self.vb
        vb._schedule_next("MNQ")
        self.assertIn("MNQ", vb._VB_TIMERS)
        self.assertIsInstance(vb._VB_TIMERS["MNQ"], threading.Timer)
        vb._VB_TIMERS["MNQ"].cancel()

    def test_schedule_next_separate_timers_per_instrument(self):
        vb = self.vb
        vb._schedule_next("MNQ")
        vb._schedule_next("MGC")
        self.assertIn("MNQ", vb._VB_TIMERS)
        self.assertIn("MGC", vb._VB_TIMERS)
        self.assertIsNot(vb._VB_TIMERS["MNQ"], vb._VB_TIMERS["MGC"])
        vb._VB_TIMERS["MNQ"].cancel()
        vb._VB_TIMERS["MGC"].cancel()

    def test_schedule_next_uses_interval_by_default(self):
        """Without delay arg, timer interval == VISUAL_BRAIN_INTERVAL."""
        vb = self.vb
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb._schedule_next("MNQ")
            args = mock_timer.call_args[0]
            interval = args[0]
            self.assertAlmostEqual(interval, float(vb.VISUAL_BRAIN_INTERVAL), places=1)

    def test_schedule_next_uses_explicit_delay(self):
        """With delay=20.0, timer interval == 20.0."""
        vb = self.vb
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb._schedule_next("MNQ", delay=20.0)
            args = mock_timer.call_args[0]
            interval = args[0]
            self.assertAlmostEqual(interval, 20.0, places=1)

    def test_schedule_next_passes_instrument_to_tick(self):
        """The timer should call _vb_tick with the correct instrument arg."""
        vb = self.vb
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb._schedule_next("MGC")
            # threading.Timer(interval, fn, args=(instrument,))
            positional = mock_timer.call_args[0]
            fn_args = (
                mock_timer.call_args[1].get("args")
                or (positional[2] if len(positional) > 2 else None)
            )
            self.assertIsNotNone(fn_args)
            self.assertIn("MGC", fn_args)


# ---------------------------------------------------------------------------
# 5. start() — staggered multi-instrument timers
# ---------------------------------------------------------------------------

class TestStart(unittest.TestCase):
    def tearDown(self) -> None:
        _cleanup_vb()

    def test_start_disabled_spawns_no_timers(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC"}, enabled=False)
        with patch("threading.Timer") as mock_timer:
            vb.start()
            mock_timer.assert_not_called()
        self.assertEqual(vb._VB_TIMERS, {})

    def test_start_single_instrument_spawns_one_timer(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ"}, enabled=True)
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            self.assertEqual(mock_timer.call_count, 1)

    def test_start_two_instruments_spawns_two_timers(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC"}, enabled=True)
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            self.assertEqual(mock_timer.call_count, 2)

    def test_start_four_instruments_spawns_four_timers(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC,MES,MYM"}, enabled=True)
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            self.assertEqual(mock_timer.call_count, 4)

    def test_start_stagger_three_instruments(self):
        """Three instruments spread evenly across the five-minute interval."""
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC,MES"}, enabled=True)
        delays = []
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            for c in mock_timer.call_args_list:
                delays.append(c[0][0])
        delays.sort()
        iv = float(vb.VISUAL_BRAIN_INTERVAL)
        slot = iv / 3.0
        self.assertAlmostEqual(delays[0], iv +  0.0, places=1)
        self.assertAlmostEqual(delays[1], iv + slot, places=1)
        self.assertAlmostEqual(delays[2], iv + (2.0 * slot), places=1)

    def test_start_stagger_four_instruments(self):
        """Four instruments spread evenly across the five-minute interval.

        All first ticks are ≥ VISUAL_BRAIN_INTERVAL so boot completes before any
        screenshot/model work begins.
        """
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC,MES,MYM"}, enabled=True)
        delays = []
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            for c in mock_timer.call_args_list:
                delays.append(c[0][0])
        delays.sort()
        iv = float(vb.VISUAL_BRAIN_INTERVAL)
        slot = iv / 4.0
        expected = [iv + (i * slot) for i in range(4)]
        for got, exp in zip(delays, expected):
            self.assertAlmostEqual(got, exp, places=1)

    def test_all_first_ticks_gte_interval(self):
        """Every instrument's first tick must be >= VISUAL_BRAIN_INTERVAL (boot-safe)."""
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC,MES,MYM"}, enabled=True)
        delays = []
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            for c in mock_timer.call_args_list:
                delays.append(c[0][0])
        iv = float(vb.VISUAL_BRAIN_INTERVAL)
        for d in delays:
            self.assertGreaterEqual(d, iv,
                f"First tick delay {d}s is less than VISUAL_BRAIN_INTERVAL {iv}s — unsafe boot timing")

    def test_start_timer_is_daemon(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ"}, enabled=True)
        mock_t = MagicMock()
        mock_t.daemon = False
        with patch.object(vb.threading, "Timer", return_value=mock_t):
            vb.start()
        self.assertTrue(mock_t.daemon)

    def test_start_disabled_is_byte_identical_no_state_change(self):
        """Flag OFF: _VB_SYMBOLS still set but _VB_TIMERS stays empty."""
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ,MGC"}, enabled=False)
        symbols_before = list(vb._VB_SYMBOLS)
        timers_before = dict(vb._VB_TIMERS)
        vb.start()
        self.assertEqual(vb._VB_SYMBOLS, symbols_before)
        self.assertEqual(vb._VB_TIMERS, timers_before)

    def test_default_config_spawns_two_timers(self):
        """Regression: default config boots MNQ and MGC observer loops only.
        """
        vb = _load_vb(enabled=True)  # no override → uses default "MNQ,MGC"
        self.assertEqual(len(vb._VB_SYMBOLS), 2,
            f"Default _VB_SYMBOLS should be 2 instruments, got {vb._VB_SYMBOLS}")
        with patch.object(vb.threading, "Timer") as mock_timer:
            mock_timer.return_value = MagicMock()
            vb.start()
            self.assertEqual(mock_timer.call_count, 2,
                f"Expected 2 timers for 2 default instruments, got {mock_timer.call_count}")

    def test_default_all_status_returns_two_symbols(self):
        """Regression: all-status symbols list contains only the configured defaults."""
        vb = _load_vb(enabled=True)
        self.assertEqual(vb._VB_SYMBOLS, ["MNQ", "MGC"])


# ---------------------------------------------------------------------------
# 6. _vb_tick — per-instrument cache write and reschedule
# ---------------------------------------------------------------------------

class TestVbTickInstrumentIsolation(unittest.TestCase):
    """Verify tick only touches the calling instrument's cache slot."""

    def tearDown(self) -> None:
        _cleanup_vb()

    def _build_vb(self, instrument: str = "MNQ"):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": instrument}, enabled=True)
        vb._db_conn_fn = None   # no DB
        vb._price_store = {instrument: {"value": 21000.0}}
        vb._bars_fn = lambda inst: [
            {"ts_event": 1000.0, "open": 21000, "high": 21020, "low": 20990, "close": 21010},
        ]
        vb.VB_DB_READY = False   # skip DB insert
        return vb

    def test_tick_mnq_does_not_touch_mgc_slot(self):
        vb = self._build_vb("MNQ")
        # Pre-populate MGC slot
        with vb._VB_LOCK:
            vb._LAST_OBSERVATION_BY_INST["MGC"] = {"bias": "BEARISH"}

        obs_result = {"instrument": "MNQ", "bias": "BULLISH", "action": "LONG_WATCH",
                      "confidence": 80, "market_state": "TRENDING_UP",
                      "last_event": "BOS", "summary": "test", "state_changed": False,
                      "state_change_reason": "", "short_term_structure": None,
                      "support_description": "", "support_price": None,
                      "resistance_description": "", "resistance_price": None,
                      "long_condition": "", "short_condition": "", "timestamp": "2026-01-01T00:00:00"}

        with (
            patch.object(vb, "capture_chart_screenshot", return_value=b"\xff\xd8\xff" + b"\x00" * 100),
            patch.object(vb, "get_history", return_value=[]),
            patch.object(vb, "analyze_visual_market", return_value=obs_result),
            patch.object(vb, "_insert_observation", return_value=1),
            patch.object(vb, "_schedule_next"),
            patch("threading.Thread"),
        ):
            vb._vb_tick("MNQ")

        # MNQ slot updated
        self.assertIn("MNQ", vb._LAST_OBSERVATION_BY_INST)
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MNQ"]["bias"], "BULLISH")
        # MGC slot untouched
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MGC"]["bias"], "BEARISH")

    def test_tick_calls_schedule_next_with_correct_instrument(self):
        vb = self._build_vb("MGC")
        obs_result = {"instrument": "MGC", "bias": "BEARISH", "action": "SHORT_WATCH",
                      "confidence": 75, "market_state": "TRENDING_DOWN",
                      "last_event": "CHOCH", "summary": "test", "state_changed": False,
                      "state_change_reason": "", "short_term_structure": None,
                      "support_description": "", "support_price": None,
                      "resistance_description": "", "resistance_price": None,
                      "long_condition": "", "short_condition": "", "timestamp": "2026-01-01T00:00:00"}

        schedule_calls = []
        def fake_schedule(inst, delay=None):
            schedule_calls.append(inst)

        with (
            patch.object(vb, "capture_chart_screenshot", return_value=b"\xff\xd8\xff" + b"\x00" * 100),
            patch.object(vb, "get_history", return_value=[]),
            patch.object(vb, "analyze_visual_market", return_value=obs_result),
            patch.object(vb, "_insert_observation", return_value=1),
            patch.object(vb, "_schedule_next", side_effect=fake_schedule),
            patch("threading.Thread"),
        ):
            vb._vb_tick("MGC")

        self.assertEqual(schedule_calls, ["MGC"])

    def test_tick_disabled_is_noop(self):
        vb = _load_vb({"VISUAL_BRAIN_SYMBOL": "MNQ"}, enabled=False)
        with patch.object(vb, "capture_chart_screenshot") as cap:
            vb._vb_tick("MNQ")
            cap.assert_not_called()
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST, {})

    def test_tick_no_screenshot_skips_analysis_but_reschedules(self):
        """When screenshot fails, skip analysis but still reschedule (finally guard)."""
        vb = self._build_vb("MNQ")
        schedule_calls = []
        def fake_schedule(inst, delay=None):
            schedule_calls.append(inst)

        with (
            patch.object(vb, "capture_chart_screenshot", return_value=None),
            patch.object(vb, "_schedule_next", side_effect=fake_schedule),
        ):
            vb._vb_tick("MNQ")

        # Rescheduled despite no screenshot
        self.assertEqual(schedule_calls, ["MNQ"])
        # Cache not written
        self.assertNotIn("MNQ", vb._LAST_OBSERVATION_BY_INST)


# ---------------------------------------------------------------------------
# 7. Cost counter — shared across instruments
# ---------------------------------------------------------------------------

class TestCostCounterShared(unittest.TestCase):
    def tearDown(self) -> None:
        _cleanup_vb()

    def test_record_cost_increments_regardless_of_instrument(self):
        vb = _load_vb()
        # Reset counters
        with vb._COST_LOCK:
            vb._vb_calls_today = 0
            vb._vb_cost_today = 0.0

        vb._record_cost(100, 200)
        vb._record_cost(150, 250)
        summary = vb.get_cost_summary()
        self.assertEqual(summary["calls_today"], 2)
        self.assertGreater(summary["cost_today_usd"], 0.0)

    def test_get_cost_summary_returns_all_instruments(self):
        """Cost summary is global — no instrument key in response."""
        vb = _load_vb()
        summary = vb.get_cost_summary()
        self.assertIn("calls_today", summary)
        self.assertIn("cost_today_usd", summary)


# ---------------------------------------------------------------------------
# 8. all-status route (Flask integration — import guard only)
# ---------------------------------------------------------------------------

class TestAllStatusRoute(unittest.TestCase):
    """Smoke test: the route_visual_brain_all_status function exists in app.py."""

    def test_all_status_route_registered(self):
        """Verify the route function is defined (grep-level check)."""
        app_path = os.path.join(
            os.path.dirname(__file__), "..", "app.py"
        )
        with open(app_path) as f:
            src = f.read()
        self.assertIn("route_visual_brain_all_status", src)
        self.assertIn("/visual-brain/all-status", src)
        self.assertIn('"instruments"', src)
        self.assertIn('"symbols"', src)

    def test_proxy_whitelist_includes_all_status(self):
        proxy_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "artifacts", "api-server", "src", "routes", "flask-proxy.ts"
        )
        if not os.path.exists(proxy_path):
            self.skipTest("flask-proxy.ts not found — skipping proxy whitelist check")
        with open(proxy_path) as f:
            src = f.read()
        self.assertIn("/visual-brain/all-status", src)


if __name__ == "__main__":
    unittest.main()
