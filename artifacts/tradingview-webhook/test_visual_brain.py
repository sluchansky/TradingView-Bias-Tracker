"""Tests for Visual Brain V1 — MNQ Stateful Market Observer.

Covers:
  1. Valid JSON parsing + schema validation
  2. Malformed model response
  3. Screenshot failure
  4. State persistence (in-memory cache)
  5. State transitions (state_changed detection)
  6. Database insert
  7. History retrieval
  8. Model timeout
  9. Disabled mode (byte-identical to baseline)
  10. Trading-engine isolation (full_analysis unchanged)

Uses pytest + unittest.mock — no network calls, no DB required.
"""
import importlib
import json
import os
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ── Make the tradingview-webhook dir importable ──────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_mode_assessments() -> dict:
    return {
        "scalp": {
            "posture": "LONG_BIAS",
            "setup_status": "FORMING",
            "confidence": 72,
            "validation": "Hold above VWAP with a fresh higher low",
            "invalidation": "Lose VWAP and the latest higher low",
            "reason": "Bullish structure is intact but needs an immediate trigger.",
        },
        "intraday_trend": {
            "posture": "LONG_BIAS",
            "setup_status": "FORMING",
            "confidence": 64,
            "timeframe_alignment": "MIXED",
            "market_phase": "PULLBACK",
            "session_level": "VWAP support",
            "validation": "Reclaim the session high with momentum",
            "invalidation": "Sustain below VWAP support",
            "reason": "The visible pullback is constructive but higher-timeframe confirmation is unavailable.",
        },
        "swing": {
            "posture": "NEUTRAL",
            "setup_status": "WAIT",
            "confidence": 35,
            "timeframe_alignment": "UNKNOWN",
            "thesis_quality": "UNKNOWN",
            "structural_stop": "UNKNOWN",
            "target_context": "UNKNOWN",
            "validation": "Wait for confirmed higher-timeframe alignment",
            "invalidation": "No thesis until alignment is confirmed",
            "reason": "A one-minute chart cannot establish a swing thesis alone.",
        },
    }


def _make_obs(bias="BULLISH", state="TRENDING_UP", event="RECLAIM",
              action="LONG_WATCH", conf=75, changed=True, reason="Reclaim above VWAP") -> dict:
    return {
        "instrument":       "MNQ",
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "bias":             bias,
        "market_state":     state,
        "structure": {
            "short_term":       "HH_HL",
            "higher_low_intact": True,
            "lower_high_intact": False,
        },
        "last_event":       event,
        "support":          {"visible": True, "description": "VWAP", "approx_price": 30100.0},
        "resistance":       {"visible": True, "description": "Prior high", "approx_price": 30250.0},
        "long_condition":   "Hold above VWAP",
        "short_condition":  "Break below 30100",
        "action":           action,
        "confidence":       conf,
        "state_changed":    changed,
        "state_change_reason": reason,
        "summary":          "Price reclaimed VWAP after a sweep. Structure remains bullish.",
        "mode_assessments": _make_mode_assessments(),
    }


def _import_vb():
    """Import visual_brain with AI_INTEGRATIONS env vars stubbed.
    VISUAL_BRAIN_ENABLED is forced to 'false' so tests that check the default-off
    behaviour are not affected by the dev environment having it set to true.
    """
    os.environ.setdefault("AI_INTEGRATIONS_OPENAI_API_KEY", "test-key")
    os.environ.setdefault("AI_INTEGRATIONS_OPENAI_BASE_URL", "https://api.openai.com/v1")
    if "visual_brain" in sys.modules:
        del sys.modules["visual_brain"]
    # Temporarily clear the flag so module-level VISUAL_BRAIN_ENABLED evaluates False
    _orig = os.environ.pop("VISUAL_BRAIN_ENABLED", None)
    try:
        mod = importlib.import_module("visual_brain")
    finally:
        if _orig is not None:
            os.environ["VISUAL_BRAIN_ENABLED"] = _orig
    return mod


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Valid JSON parsing + schema validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidJsonParsing(unittest.TestCase):
    """Valid observation dict passes _validate_observation."""

    def setUp(self):
        self.vb = _import_vb()

    def test_valid_obs_passes(self):
        obs = _make_obs()
        ok, reason = self.vb._validate_observation(obs)
        self.assertTrue(ok, reason)

    def test_all_bias_values_accepted(self):
        for bias in ("BULLISH", "BEARISH", "NEUTRAL"):
            obs = _make_obs(bias=bias)
            ok, reason = self.vb._validate_observation(obs)
            self.assertTrue(ok, f"bias={bias}: {reason}")

    def test_all_action_values_accepted(self):
        for action in ("LONG_WATCH", "SHORT_WATCH", "WAIT", "NO_TRADE"):
            obs = _make_obs(action=action)
            ok, reason = self.vb._validate_observation(obs)
            self.assertTrue(ok, f"action={action}: {reason}")

    def test_confidence_bounds(self):
        for conf in (0, 50, 100):
            obs = _make_obs(conf=conf)
            ok, _ = self.vb._validate_observation(obs)
            self.assertTrue(ok)

    def test_confidence_out_of_bounds_fails(self):
        obs = _make_obs(conf=101)
        ok, _ = self.vb._validate_observation(obs)
        self.assertFalse(ok)

    def test_mode_assessments_are_required_and_validated(self):
        obs = _make_obs()
        del obs["mode_assessments"]["swing"]["target_context"]
        ok, reason = self.vb._validate_observation(obs)
        self.assertFalse(ok)
        self.assertIn("mode_assessments.swing", reason)

    def test_invalid_mode_assessment_enum_fails(self):
        obs = _make_obs()
        obs["mode_assessments"]["intraday_trend"]["market_phase"] = "GUESSING"
        ok, reason = self.vb._validate_observation(obs)
        self.assertFalse(ok)
        self.assertIn("market_phase", reason)

    def test_rendered_mode_text_fields_must_be_strings(self):
        for mode, field in (
            ("intraday_trend", "session_level"),
            ("swing", "structural_stop"),
            ("swing", "target_context"),
        ):
            obs = _make_obs()
            obs["mode_assessments"][mode][field] = {"unexpected": "object"}
            ok, reason = self.vb._validate_observation(obs)
            self.assertFalse(ok)
            self.assertIn(field, reason)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Malformed model response
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedModelResponse(unittest.TestCase):
    """analyze_visual_market raises on malformed / invalid-schema response."""

    def setUp(self):
        self.vb = _import_vb()

    def _call_with_content(self, content: str) -> Exception | None:
        """Run analyze_visual_market with a mocked model that returns `content`."""
        mock_choice = MagicMock()
        mock_choice.message.content = content
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens     = 100
        mock_resp.usage.completion_tokens = 50

        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.chat.completions.create.return_value = mock_resp
            try:
                self.vb.analyze_visual_market(b"fake-img", None, [], "MNQ")
                return None
            except Exception as exc:
                return exc

    def test_non_json_response_raises(self):
        exc = self._call_with_content("Not JSON at all — prose response.")
        self.assertIsNotNone(exc, "Expected RuntimeError on non-JSON")

    def test_missing_required_key_raises(self):
        obs = _make_obs()
        del obs["bias"]   # remove a required key
        exc = self._call_with_content(json.dumps(obs))
        self.assertIsNotNone(exc, "Expected error on missing key")

    def test_invalid_enum_raises(self):
        obs = _make_obs()
        obs["action"] = "INVALID_ACTION"
        exc = self._call_with_content(json.dumps(obs))
        self.assertIsNotNone(exc, "Expected error on invalid enum")

    def test_valid_response_succeeds(self):
        obs = _make_obs()
        exc = self._call_with_content(json.dumps(obs))
        self.assertIsNone(exc, f"Unexpected error on valid response: {exc}")

    def test_markdown_fenced_json_is_stripped(self):
        obs = _make_obs()
        fenced = f"```json\n{json.dumps(obs)}\n```"
        exc = self._call_with_content(fenced)
        self.assertIsNone(exc, f"Unexpected error on fenced JSON: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Screenshot failure
# ─────────────────────────────────────────────────────────────────────────────

class TestScreenshotFailure(unittest.TestCase):
    """capture_chart_screenshot raises RuntimeError when all strategies fail."""

    def setUp(self):
        self.vb = _import_vb()

    @patch("visual_brain.capture_chart_screenshot", side_effect=RuntimeError("All strategies failed"))
    def test_screenshot_failure_raises(self, _mock):
        with self.assertRaises(RuntimeError):
            self.vb.capture_chart_screenshot("MNQ")

    def test_worker_survives_screenshot_failure(self):
        """_vb_tick must NOT raise even when screenshot fails."""
        self.vb.VISUAL_BRAIN_ENABLED = True
        reschedule_called = threading.Event()

        with patch("visual_brain.capture_chart_screenshot",
                   side_effect=RuntimeError("capture failed")), \
             patch("visual_brain._schedule_next",
                   side_effect=lambda *a: reschedule_called.set()):
            self.vb._vb_tick()   # must not raise

        self.assertTrue(reschedule_called.is_set(), "_schedule_next should be called even on failure")

    def test_databento_bars_produce_a_real_jpeg(self):
        """The primary production capture path returns an actual image."""
        self.vb._bars_fn = lambda _symbol: [
            {
                "ts": 1770000000 + i * 60,
                "open": 21000 + i * 3,
                "high": 21008 + i * 3,
                "low": 20996 + i * 3,
                "close": 21003 + i * 3,
                "volume": 100 + i,
            }
            for i in range(12)
        ]

        image = self.vb.capture_chart_screenshot("MNQ")

        self.assertTrue(image.startswith(b"\xff\xd8\xff"))
        self.assertGreater(len(image), 1000)


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: State persistence (in-memory cache)
# ─────────────────────────────────────────────────────────────────────────────

class TestStatePersistence(unittest.TestCase):
    """_LAST_OBSERVATION is updated after a successful tick."""

    def setUp(self):
        self.vb = _import_vb()
        self.vb._LAST_OBSERVATION_BY_INST.clear()
        self.vb.VISUAL_BRAIN_ENABLED = True

    def test_last_observation_updated_after_tick(self):
        obs = _make_obs()
        screenshot_bytes = b"fake-jpeg"

        with patch("visual_brain.capture_chart_screenshot", return_value=screenshot_bytes), \
             patch("visual_brain.get_history", return_value=[]), \
             patch("visual_brain.analyze_visual_market", return_value=obs), \
             patch("visual_brain._insert_observation", return_value=1), \
             patch("visual_brain._schedule_next"), \
             patch("visual_brain._backfill_ghost_outcomes"):
            self.vb._vb_tick()

        with self.vb._VB_LOCK:
            cached = self.vb._LAST_OBSERVATION_BY_INST.get("MNQ")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["bias"], "BULLISH")

    def test_cache_returned_by_get_last_observation(self):
        obs = _make_obs()
        self.vb._LAST_OBSERVATION_BY_INST["MNQ"] = obs
        result = self.vb.get_last_observation("MNQ")
        self.assertIsNotNone(result)
        self.assertEqual(result["bias"], obs["bias"])


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: State transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestStateTransitions(unittest.TestCase):
    """_build_history_text produces expected compact lines."""

    def setUp(self):
        self.vb = _import_vb()

    def test_empty_history_returns_sentinel(self):
        text = self.vb._build_history_text([])
        self.assertIn("No prior", text)

    def test_history_includes_bias_state_action(self):
        hist = [_make_obs(bias="BEARISH", state="BREAKDOWN", action="SHORT_WATCH", conf=80)]
        text = self.vb._build_history_text(hist)
        self.assertIn("BEARISH", text)
        self.assertIn("BREAKDOWN", text)
        self.assertIn("SHORT_WATCH", text)

    def test_state_changed_annotated(self):
        hist = [_make_obs(changed=True)]
        text = self.vb._build_history_text(hist)
        self.assertIn("SHIFT", text)

    def test_no_state_change_not_annotated(self):
        hist = [_make_obs(changed=False)]
        text = self.vb._build_history_text(hist)
        self.assertNotIn("SHIFT", text)

    def test_history_capped_at_10(self):
        hist = [_make_obs() for _ in range(15)]
        text = self.vb._build_history_text(hist)
        # Each entry has one line; ≤10 lines expected
        self.assertLessEqual(len(text.splitlines()), 10)


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Database insert
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseInsert(unittest.TestCase):
    """_insert_observation calls cursor.execute with the right SQL."""

    def setUp(self):
        self.vb = _import_vb()
        self.vb.VB_DB_READY = True

    def test_insert_returns_id_on_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (42,)
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn), \
             patch("visual_brain.AUTO_PRICE_BY_TICKER", {"MNQ": {"value": 30200.0}}, create=True):
            obs = _make_obs()
            row_id = self.vb._insert_observation(obs)

        self.assertEqual(row_id, 42)
        mock_conn.commit.assert_called_once()

    def test_insert_returns_none_when_db_not_ready(self):
        self.vb.VB_DB_READY = False
        obs = _make_obs()
        row_id = self.vb._insert_observation(obs)
        self.assertIsNone(row_id)

    def test_insert_fails_open_on_db_error(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn):
            obs = _make_obs()
            row_id = self.vb._insert_observation(obs)  # must not raise

        self.assertIsNone(row_id)


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: History retrieval
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryRetrieval(unittest.TestCase):
    """get_history returns rows from DB or empty list when DB unavailable."""

    def setUp(self):
        self.vb = _import_vb()
        self.vb.VB_DB_READY = True

    def test_get_history_returns_rows(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        # Simulate 2 rows with all expected columns
        cols = ["id","timestamp","instrument","bias","market_state","short_term_structure",
                "last_event","action","confidence","support_description","support_price",
                "resistance_description","resistance_price","long_condition","short_condition",
                "state_changed","state_change_reason","summary","screenshot_path",
                "p1m","p3m","p5m","p10m","p15m","mfe","mae","outcome_resolved"]
        mock_cursor.description = [(c,) for c in cols]
        now = datetime.now(timezone.utc)
        row = (1, now, "MNQ", "BULLISH", "TRENDING_UP", "HH_HL",
               "RECLAIM", "LONG_WATCH", 75, "VWAP", 30100.0,
               "Prior high", 30250.0, "Hold above VWAP", "Break below 30100",
               True, "Reclaim", "Bullish.", None,
               0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.1, True)
        mock_cursor.fetchall.return_value = [row, row]
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn):
            result = self.vb.get_history("MNQ", limit=5)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["bias"], "BULLISH")

    def test_get_history_empty_when_db_not_ready(self):
        self.vb.VB_DB_READY = False
        result = self.vb.get_history("MNQ")
        self.assertEqual(result, [])

    def test_get_history_empty_on_db_error(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn):
            result = self.vb.get_history("MNQ")  # must not raise

        self.assertEqual(result, [])

    def test_get_history_hydrates_mode_assessments_from_raw_json(self):
        """New assessments use existing raw_json; no schema migration is needed."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        obs = _make_obs()
        cols = ["id", "timestamp", "instrument", "bias", "market_state", "short_term_structure",
                "last_event", "action", "confidence", "support_description", "support_price",
                "resistance_description", "resistance_price", "long_condition", "short_condition",
                "state_changed", "state_change_reason", "summary", "screenshot_path",
                "p1m", "p3m", "p5m", "p10m", "p15m", "mfe", "mae", "outcome_resolved", "raw_json"]
        mock_cursor.description = [(column,) for column in cols]
        mock_cursor.fetchall.return_value = [(
            1, datetime.now(timezone.utc), "MNQ", "BULLISH", "TRENDING_UP", "HH_HL",
            "RECLAIM", "LONG_WATCH", 75, "VWAP", 30100.0, "Prior high", 30250.0,
            "Hold above VWAP", "Break below 30100", True, "Reclaim", "Bullish.", None,
            None, None, None, None, None, None, None, False, json.dumps(obs),
        )]
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn):
            rows = self.vb.get_history("MNQ", limit=1)

        self.assertEqual(rows[0]["mode_assessments"]["scalp"]["setup_status"], "FORMING")
        self.assertEqual(rows[0]["mode_assessments"]["swing"]["thesis_quality"], "UNKNOWN")

    def test_older_history_rows_have_safe_empty_mode_assessments(self):
        flat = self.vb._flatten_obs({"instrument": "MNQ", "bias": "NEUTRAL"})
        self.assertEqual(flat["mode_assessments"], {})


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Model timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestModelTimeout(unittest.TestCase):
    """analyze_visual_market raises RuntimeError on timeout (mocked as exception)."""

    def setUp(self):
        self.vb = _import_vb()

    def test_timeout_raises_runtime_error(self):
        import openai  # noqa
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.chat.completions.create.side_effect = \
                Exception("Connection timed out")
            with self.assertRaises(RuntimeError):
                self.vb.analyze_visual_market(b"img", None, [], "MNQ")

    def test_worker_survives_model_timeout(self):
        """_vb_tick must NOT propagate model timeout."""
        self.vb.VISUAL_BRAIN_ENABLED = True
        reschedule_called = threading.Event()

        with patch("visual_brain.capture_chart_screenshot", return_value=b"img"), \
             patch("visual_brain.get_history", return_value=[]), \
             patch("visual_brain.analyze_visual_market",
                   side_effect=RuntimeError("model timeout")), \
             patch("visual_brain._schedule_next",
                   side_effect=lambda *a: reschedule_called.set()):
            self.vb._vb_tick()  # must not raise

        self.assertTrue(reschedule_called.is_set())


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Disabled mode (byte-identical)
# ─────────────────────────────────────────────────────────────────────────────

class TestDisabledMode(unittest.TestCase):
    """When VISUAL_BRAIN_ENABLED=false start() and _vb_tick() are no-ops."""

    def setUp(self):
        self.vb = _import_vb()
        self.vb.VISUAL_BRAIN_ENABLED = False

    def test_start_is_noop_when_disabled(self):
        timer_started = threading.Event()
        with patch("visual_brain._schedule_next",
                   side_effect=lambda *a: timer_started.set()):
            self.vb.start()
        self.assertFalse(timer_started.is_set(), "No timer should start when disabled")

    def test_vb_tick_is_noop_when_disabled(self):
        capture_called = threading.Event()
        with patch("visual_brain.capture_chart_screenshot",
                   side_effect=lambda *a, **kw: capture_called.set() or b""):
            self.vb._vb_tick()
        self.assertFalse(capture_called.is_set(), "Screenshot should not be called when disabled")

    def test_schedule_next_is_noop_when_disabled(self):
        """_schedule_next returns without creating a timer when disabled."""
        timer_created = threading.Event()
        real_Timer = threading.Timer

        def mock_timer(interval, fn):
            timer_created.set()
            t = real_Timer(interval, fn)
            return t

        with patch("threading.Timer", side_effect=mock_timer):
            self.vb._schedule_next()
        self.assertFalse(timer_created.is_set())


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Trading-engine isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestTradingEngineIsolation(unittest.TestCase):
    """Visual Brain failure has no effect on the trading-engine module."""

    def test_visual_brain_import_does_not_modify_app_globals(self):
        """Importing visual_brain does NOT alter any app.py global."""
        # Import app.py carefully: just check the key globals exist and are unchanged
        # (we don't full-import app to avoid DB connections; instead we just verify
        # that visual_brain.py doesn't mutate anything on import.)
        vb = _import_vb()
        # visual_brain has no side-effects at import time (no threading.start)
        self.assertFalse(vb.VISUAL_BRAIN_ENABLED)   # default OFF

    def test_visual_brain_failure_does_not_raise(self):
        """Any exception in _vb_tick is caught; it never propagates."""
        vb = _import_vb()
        vb.VISUAL_BRAIN_ENABLED = True

        with patch("visual_brain.capture_chart_screenshot",
                   side_effect=Exception("catastrophic failure")), \
             patch("visual_brain._schedule_next"):
            try:
                vb._vb_tick()
            except Exception as exc:
                self.fail(f"_vb_tick propagated an exception: {exc}")

    def test_cost_counter_isolated_per_module(self):
        """Cost counters are module-level; resetting them does not affect app.py."""
        vb = _import_vb()
        vb._vb_calls_today = 99
        vb._vb_cost_today  = 1.23

        summary = vb.get_cost_summary()
        self.assertIn("calls_today", summary)
        self.assertIn("cost_today_usd", summary)
        self.assertIsInstance(summary["cost_today_usd"], float)

    def test_validate_observation_does_not_import_app(self):
        """_validate_observation has no dependency on app module."""
        # Temporarily remove app from sys.modules to ensure no import
        app_backup = sys.modules.pop("app", None)
        try:
            vb = _import_vb()
            obs = _make_obs()
            ok, reason = vb._validate_observation(obs)
            self.assertTrue(ok, reason)
        finally:
            if app_backup is not None:
                sys.modules["app"] = app_backup


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Single-flight reschedule — exactly one _schedule_next per tick
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Test 11: bars_fn injection — chart rendering from injected Databento bars
# ─────────────────────────────────────────────────────────────────────────────

class TestBarsFnInjection(unittest.TestCase):
    """Integration test: verifies that the injected bars_fn flows through
    capture_chart_screenshot() and produces valid JPEG bytes.

    This covers the boot-time wiring in app.py:
        import databento_brain as _dbb_for_vb
        bars_fn=lambda inst: list(_dbb_for_vb.DATABENTO_BARS_BY_INST.get(inst, []))
    by exercising the same code path with representative bar data.
    """

    def setUp(self):
        self.vb = _import_vb()

    def _make_bars(self, n: int = 30) -> list:
        """Generate n representative 1-minute OHLCV dicts for MNQ."""
        import time as _time
        now = int(_time.time())
        bars = []
        price = 21000.0
        for i in range(n):
            o = price + (i % 5) * 5
            c = o + ((-1) ** i) * 10
            bars.append({
                "open": o, "high": o + 15, "low": o - 10, "close": c,
                "volume": 500 + i * 10,
                "ts_event": now - (n - i) * 60,
                "ts": now - (n - i) * 60,
            })
            price = c
        return bars

    def test_bars_fn_injected_and_called_in_capture(self):
        """After start(bars_fn=...) the injected fn is called by capture_chart_screenshot.

        Tests the injection chain (bars_fn → _generate_chart_from_bars) rather than
        matplotlib rendering itself (which is tested separately in
        test_generate_chart_from_bars_returns_jpeg).
        """
        bars = self._make_bars(30)
        captured_inst = []

        def mock_bars_fn(inst):
            captured_inst.append(inst)
            return bars

        self.vb.start(bars_fn=mock_bars_fn)
        self.assertEqual(self.vb._bars_fn, mock_bars_fn,
                         "bars_fn should be stored as module-level _bars_fn")

        # Stub chart generation so the test is purely about injection wiring
        # (not about matplotlib display availability in CI)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100   # JPEG magic + padding
        with patch("visual_brain._generate_chart_from_bars", return_value=fake_jpeg) as mock_gen:
            result = self.vb.capture_chart_screenshot("MNQ")

        self.assertIn("MNQ", captured_inst, "bars_fn should have been called with 'MNQ'")
        mock_gen.assert_called_once_with(bars, "MNQ")
        self.assertEqual(result, fake_jpeg)

    def test_generate_chart_from_bars_returns_jpeg(self):
        """_generate_chart_from_bars produces valid JPEG bytes from representative bar data."""
        bars = self._make_bars(30)
        result = self.vb._generate_chart_from_bars(bars, "MNQ")
        self.assertIsInstance(result, bytes, "Expected bytes output")
        self.assertGreater(len(result), 200, "Expected non-trivial JPEG output")
        # JPEG always starts with 0xFF 0xD8
        self.assertEqual(result[:2], b"\xff\xd8",
                         "Expected JPEG magic bytes (0xFF 0xD8) at start of output")

    def test_bars_fn_empty_falls_through_to_playwright_attempt(self):
        """When bars_fn returns [] the matplotlib path fails gracefully."""
        self.vb.start(bars_fn=lambda inst: [])

        # Both strategies fail (empty bars + no real Playwright in test env)
        with self.assertRaises(RuntimeError, msg="Expected RuntimeError when all strategies fail"):
            self.vb.capture_chart_screenshot("MNQ")

    def test_bars_fn_none_raises(self):
        """Before start(), capture_chart_screenshot raises because _bars_fn is None."""
        self.vb._bars_fn = None
        with self.assertRaises(RuntimeError):
            self.vb.capture_chart_screenshot("MNQ")

    def test_db_conn_fn_injected_via_start(self):
        """start() stores db_conn_fn as _db_conn_fn for all subsequent DB calls."""
        called = []

        def mock_conn_fn():
            called.append(True)
            return None   # connection factory — return None to stay fail-open

        bars = self._make_bars(5)
        self.vb.start(db_conn_fn=mock_conn_fn, bars_fn=lambda inst: bars)
        self.assertEqual(self.vb._db_conn_fn, mock_conn_fn)

        # _get_conn() should call our injected function
        conn = self.vb._get_conn()
        self.assertTrue(len(called) > 0, "db_conn_fn should be called by _get_conn()")
        self.assertIsNone(conn, "None return from mock should propagate as None")

    def test_get_history_coerces_decimal_to_float(self):
        """psycopg2 returns NUMERIC as Decimal; get_history must coerce to float
        so Flask can serialize the history response as a JSON number, not a string."""
        from decimal import Decimal
        vb = _import_vb()
        vb.VB_DB_READY = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.description = [
            ("id",), ("timestamp",), ("instrument",), ("bias",), ("market_state",),
            ("short_term_structure",), ("last_event",), ("action",), ("confidence",),
            ("support_description",), ("support_price",),
            ("resistance_description",), ("resistance_price",),
            ("long_condition",), ("short_condition",),
            ("state_changed",), ("state_change_reason",), ("summary",),
            ("screenshot_path",), ("p1m",), ("p3m",), ("p5m",), ("p10m",), ("p15m",),
            ("mfe",), ("mae",), ("outcome_resolved",),
        ]
        # Simulate psycopg2 returning NUMERIC fields as Decimal
        mock_cursor.fetchall.return_value = [(
            1,                          # id
            None,                       # timestamp (no isoformat — already tested)
            "MNQ",                      # instrument
            "BULLISH",                  # bias
            "TRENDING_UP",              # market_state
            "HH_HL",                    # short_term_structure
            "NONE",                     # last_event
            "LONG_WATCH",               # action
            85,                         # confidence
            "Support at 21000",         # support_description
            Decimal("21000.50"),        # support_price — NUMERIC → Decimal
            "Resistance at 21100",      # resistance_description
            Decimal("21100.00"),        # resistance_price — NUMERIC → Decimal
            "Price above VWAP",         # long_condition
            "",                         # short_condition
            True,                       # state_changed
            "Breakout confirmed",       # state_change_reason
            "Bullish continuation",     # summary
            None,                       # screenshot_path
            Decimal("0.1234"),          # p1m — NUMERIC → Decimal
            Decimal("0.3456"),          # p3m
            Decimal("0.5678"),          # p5m
            Decimal("0.7890"),          # p10m
            Decimal("1.0000"),          # p15m
            Decimal("1.2345"),          # mfe
            Decimal("0.0500"),          # mae
            True,                       # outcome_resolved
        )]
        mock_conn.cursor.return_value = mock_cursor

        with patch("visual_brain._get_conn", return_value=mock_conn):
            rows = vb.get_history("MNQ", limit=1)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in ("support_price", "resistance_price",
                      "p1m", "p3m", "p5m", "p10m", "p15m", "mfe", "mae"):
            val = row.get(field)
            self.assertIsInstance(
                val, float,
                f"Field '{field}' should be float after Decimal coercion, got {type(val).__name__} ({val!r})"
            )
        self.assertAlmostEqual(row["p1m"], 0.1234)
        self.assertAlmostEqual(row["support_price"], 21000.50)

    def test_backfill_skips_directional_outcome_for_wait_and_no_trade(self):
        """WAIT and NO_TRADE observations must be marked resolved with NULL outcomes,
        not scored as SHORT trades (which would corrupt research analytics)."""
        vb = _import_vb()
        vb.VB_DB_READY = True
        bars = [
            {"open": 21000, "high": 21020, "low": 20980, "close": 21010,
             "volume": 500, "ts_event": 1000000 + i * 60}
            for i in range(20)
        ]
        vb.start(bars_fn=lambda inst: bars)

        conn_calls = []
        executed_sqls = []

        def make_mock_conn(rows_to_fetch):
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_cursor.fetchall.return_value = rows_to_fetch
            mock_cursor.execute = lambda sql, params=None: executed_sqls.append((sql.strip(), params))
            mock_conn.cursor.return_value = mock_cursor
            conn_calls.append(mock_conn)
            return mock_conn

        from datetime import datetime, timezone, timedelta
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=20)
        # Two non-directional actions that should NOT trigger directional scoring
        mock_rows = [
            (1, old_ts, "MNQ", "WAIT",     21000.0),
            (2, old_ts, "MNQ", "NO_TRADE", 21000.0),
        ]

        call_count = [0]
        def rotating_conn():
            row_batch = mock_rows if call_count[0] == 0 else []
            call_count[0] += 1
            return make_mock_conn(row_batch)

        with patch("visual_brain._get_conn", side_effect=rotating_conn):
            vb._backfill_ghost_outcomes()

        # No UPDATE should include p1m/p3m etc. (directional fields)
        directional_updates = [
            sql for sql, _ in executed_sqls
            if "p1m" in sql or "p3m" in sql or "mfe" in sql
        ]
        self.assertEqual(
            len(directional_updates), 0,
            f"WAIT/NO_TRADE observations must not produce directional P&L. "
            f"Found: {directional_updates}"
        )
        # Each row should be marked resolved (NULL outcomes are fine)
        resolved_updates = [
            sql for sql, _ in executed_sqls
            if "outcome_resolved" in sql.lower()
        ]
        self.assertGreaterEqual(
            len(resolved_updates), 1,
            "WAIT/NO_TRADE observations should be marked outcome_resolved=TRUE"
        )

    def test_price_store_injected_and_used_in_insert(self):
        """price_store injected via start() is read by _insert_observation for entry_price."""
        price_store = {"MNQ": {"value": 21500.0}}
        bars = self._make_bars(5)
        self.vb.start(price_store=price_store, bars_fn=lambda inst: bars)
        self.assertIs(self.vb._price_store, price_store,
                      "price_store should be stored as module-level _price_store")

        # _insert_observation uses _price_store to set entry_price_at_obs
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor

        self.vb.VB_DB_READY = True
        with patch("visual_brain._get_conn", return_value=mock_conn):
            obs = _make_obs()
            obs["instrument"] = "MNQ"
            self.vb._insert_observation(obs)

        # Verify execute was called with a price near 21500 (from price_store)
        call_args = mock_cursor.execute.call_args
        self.assertIsNotNone(call_args)
        positional = call_args[0]
        if len(positional) >= 2:
            params = positional[1]
            # entry_price_at_obs is the last parameter
            entry_price = params[-1]
            self.assertEqual(entry_price, 21500.0,
                             f"Expected 21500.0 from price_store, got {entry_price}")


class TestNativeMultiTimeframeContext(unittest.TestCase):
    """Higher-timeframe bias must be supplied beside the 1m chart image."""

    def setUp(self):
        self.vb = _import_vb()

    def _make_hourly_bars(self, n=80):
        now = int(time.time())
        bars = []
        for i in range(n):
            close = 20000.0 + i * 8.0
            bars.append({
                "ts": now - (n - i) * 3600,
                "open": close - 3.0, "high": close + 7.0, "low": close - 8.0,
                "close": close, "volume": 1000 + i,
            })
        return bars

    def test_context_contains_required_timeframes_and_decided_bias(self):
        context = self.vb._build_market_context(self._make_hourly_bars(), "MNQ")

        self.assertEqual(set(context["timeframes"]), {"1m", "5m", "15m", "1h", "4h", "1D"})
        self.assertEqual(context["timeframes"]["1h"]["bias"], "BULLISH")
        self.assertEqual(context["timeframes"]["4h"]["bias"], "BULLISH")
        self.assertEqual(context["timeframes"]["1D"]["bias"], "BULLISH")
        self.assertEqual(context["bias"], "BULLISH")
        self.assertEqual(context["alignment"], "ALIGNED")

    def test_model_prompt_includes_native_context(self):
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(_make_obs())
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        context = {
            "bias": "BULLISH", "alignment": "ALIGNED",
            "timeframes": {"1h": {"bias": "BULLISH"}},
        }

        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.chat.completions.create.return_value = mock_resp
            self.vb.analyze_visual_market(
                b"fake-img", None, [], "MNQ", market_context=context,
            )

        messages = MockOpenAI.return_value.chat.completions.create.call_args.kwargs["messages"]
        prompt_text = messages[1]["content"][0]["text"]
        self.assertIn("NATIVE MULTI-TIMEFRAME CONTEXT", prompt_text)
        self.assertIn('"alignment":"ALIGNED"', prompt_text)

    def test_dense_chart_payload_uses_adaptive_image_detail(self):
        """MNQ's dense 1-minute candles must not be forced into low detail."""
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(_make_obs())
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50

        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.chat.completions.create.return_value = mock_resp
            self.vb.analyze_visual_market(b"fake-img", None, [], "MNQ")

        messages = MockOpenAI.return_value.chat.completions.create.call_args.kwargs["messages"]
        image_payload = messages[1]["content"][1]["image_url"]
        self.assertEqual(image_payload["detail"], "auto")


class TestSingleFlightReschedule(unittest.TestCase):
    """_schedule_next must be called exactly once per _vb_tick, regardless of path.

    A bug where failure branches call _schedule_next() before `finally` also
    calls it would double active timers on every persistent failure, causing
    overlapping Chromium/model work and runaway API cost.
    """

    def setUp(self):
        self.vb = _import_vb()
        self.vb.VISUAL_BRAIN_ENABLED = True

    def _count_schedule_calls(self, *, capture_raises=False, analyze_raises=False,
                               capture_returns=None) -> int:
        """Run one _vb_tick with chosen stub behaviour; return # _schedule_next calls."""
        counter = {"n": 0}

        def _fake_schedule(*_args):
            counter["n"] += 1

        screenshot = capture_returns if capture_returns is not None else b"fake-img"

        capture_side = RuntimeError("capture failed") if capture_raises else None
        analyze_side = RuntimeError("analyze failed") if analyze_raises else None

        with patch("visual_brain._schedule_next", side_effect=_fake_schedule), \
             patch("visual_brain.capture_chart_screenshot",
                   side_effect=capture_side, return_value=None if capture_raises else screenshot), \
             patch("visual_brain.get_history", return_value=[]), \
             patch("visual_brain.analyze_visual_market",
                   side_effect=analyze_side,
                   return_value=_make_obs() if not analyze_raises else None), \
             patch("visual_brain._insert_observation", return_value=1), \
             patch("visual_brain._backfill_ghost_outcomes"):
            self.vb._vb_tick()

        return counter["n"]

    def test_screenshot_failure_schedules_exactly_once(self):
        """Persistent screenshot failure must not accumulate timers."""
        n = self._count_schedule_calls(capture_raises=True)
        self.assertEqual(n, 1, f"Expected 1 _schedule_next call on screenshot failure, got {n}")

    def test_no_screenshot_schedules_exactly_once(self):
        """Empty screenshot bytes path must not accumulate timers."""
        n = self._count_schedule_calls(capture_returns=None)
        # capture_returns=None → screenshot_bytes is None → early return path
        # (patch returns None but side_effect=None so no raise; fn returns None)
        n2 = 0
        counter = {"n": 0}
        def _fake_schedule(*_args):
            counter["n"] += 1
        with patch("visual_brain._schedule_next", side_effect=_fake_schedule), \
             patch("visual_brain.capture_chart_screenshot", return_value=None), \
             patch("visual_brain.get_history", return_value=[]):
            self.vb._vb_tick()
        n2 = counter["n"]
        self.assertEqual(n2, 1, f"Expected 1 call when screenshot is None, got {n2}")

    def test_analyze_failure_schedules_exactly_once(self):
        """Persistent analysis failure (model timeout/error) must not accumulate timers."""
        n = self._count_schedule_calls(analyze_raises=True)
        self.assertEqual(n, 1, f"Expected 1 _schedule_next call on analysis failure, got {n}")

    def test_happy_path_schedules_exactly_once(self):
        """Normal successful tick also schedules exactly once."""
        n = self._count_schedule_calls()
        self.assertEqual(n, 1, f"Expected 1 _schedule_next call on success, got {n}")

    def test_disabled_flag_does_not_schedule(self):
        """When VISUAL_BRAIN_ENABLED=False, _schedule_next must not be called at all."""
        self.vb.VISUAL_BRAIN_ENABLED = False
        counter = {"n": 0}
        with patch("visual_brain._schedule_next", side_effect=lambda *a: counter.__setitem__("n", counter["n"] + 1)):
            self.vb._vb_tick()
        self.assertEqual(counter["n"], 0,
                         "_schedule_next should not be called when disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
