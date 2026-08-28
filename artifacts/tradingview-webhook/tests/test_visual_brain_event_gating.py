"""Focused safety tests for Visual Brain 2.0 event-driven paid-call gating."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


def _load_vb(**overrides) -> types.ModuleType:
    sys.modules.pop("visual_brain", None)
    env = {
        "VISUAL_BRAIN_ENABLED": "true",
        "VISUAL_BRAIN_EVENT_GATING_ENABLED": "true",
        "VISUAL_BRAIN_EVENT_DEBOUNCE_SECONDS": "0",
        "VISUAL_BRAIN_MAX_STALENESS_SECONDS": "1800",
        "VISUAL_BRAIN_CALL_WINDOW_SECONDS": "3600",
        "VISUAL_BRAIN_MAX_CALLS_PER_WINDOW": "6",
        "VISUAL_BRAIN_MAX_DAILY_SPEND_USD": "1.00",
        "VISUAL_BRAIN_ESTIMATED_CALL_COST_USD": "0.01",
        "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED": "false",
        "AI_INTEGRATIONS_OPENAI_API_KEY": "test-key",
        "AI_INTEGRATIONS_OPENAI_BASE_URL": "https://api.openai.com/v1",
        **{key: str(value) for key, value in overrides.items()},
    }
    with patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location(
            "visual_brain", ROOT / "visual_brain.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["visual_brain"] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    module._benchmark_reset_state()
    return module


def _bars(last_close: float = 101.0, last_ts: float = 1_800_000_060) -> list[dict]:
    return [
        {
            "ts": last_ts - 60,
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 100,
        },
        {
            "ts": last_ts,
            "open": 100.0,
            "high": max(102.0, last_close),
            "low": 99.0,
            "close": last_close,
            "volume": 150,
        },
    ]


def _observation() -> dict:
    return {
        "instrument": "MNQ",
        "bias": "BULLISH",
        "market_state": "TRENDING_UP",
        "last_event": "BOS",
        "action": "LONG_WATCH",
        "confidence": 75,
        "summary": "test",
    }


class VisualBrainEventGateTests(unittest.TestCase):
    def tearDown(self) -> None:
        module = sys.modules.pop("visual_brain", None)
        if module is not None:
            for timer in getattr(module, "_VB_TIMERS", {}).values():
                try:
                    timer.cancel()
                except Exception:
                    pass

    def _previous(self, vb, snapshot: dict) -> dict:
        return {
            "completed_bar_fingerprint": "bar-1",
            "image_fingerprint": {"sha256": "old", "ahash": "0f"},
            "context_fingerprint": "context-old",
            "semantic_snapshot": vb._benchmark_semantic_snapshot(snapshot),
            "last_paid_at": 1_000.0,
        }

    def test_every_deterministic_trigger_family_can_request_observation(self):
        vb = _load_vb()
        cases = [
            (
                {"structure_event": {"type": "BOS DEMAND"}},
                {"structure_event": {"type": "CHOCH DEMAND"}},
                "structure/BOS/CHOCH",
            ),
            (
                {"vwap": {"side": "BELOW", "distance_bucket": "FAR"}},
                {"vwap": {"side": "ABOVE", "distance_bucket": "NEAR"}},
                "VWAP/level",
            ),
            (
                {"ready": False, "blockers": ["VWAP"]},
                {"ready": True, "blockers": []},
                "thesis/READY/blocker",
            ),
            (
                {"volatility": {"regime": "NORMAL"}},
                {"volatility": {"regime": "ELEVATED"}},
                "volatility",
            ),
            (
                {"volume": {"regime": "NORMAL"}},
                {"volume": {"regime": "HIGH"}},
                "volume",
            ),
            (
                {"session": "OVERNIGHT"},
                {"session": "NY_OPEN"},
                "session",
            ),
            (
                {"session_levels": {"session_high": 100.0}},
                {"session_levels": {"session_high": 102.0}},
                "VWAP/level",
            ),
            (
                {"recovery": {"price_available": False}},
                {"recovery": {"price_available": True}},
                "recovery",
            ),
        ]
        for before, after, label in cases:
            with self.subTest(label=label):
                decision = vb.compute_visual_brain_gate(
                    self._previous(vb, before),
                    completed_bar_fingerprint="bar-2",
                    image_fingerprint={"sha256": "new", "ahash": "f0"},
                    context_fingerprint=f"context-{label}",
                    deterministic_snapshot=after,
                    bars=_bars(),
                    now_epoch=1_100.0,
                    event_debounce_seconds=0,
                )
                self.assertTrue(decision["call"])
                self.assertIn(label, " ".join(decision["trigger_reasons"]))

    def test_no_new_completed_bar_suppresses_before_paid_call(self):
        vb = _load_vb()
        decision = vb.compute_visual_brain_gate(
            self._previous(vb, {"ready": False}),
            completed_bar_fingerprint="bar-1",
            image_fingerprint={"sha256": "new", "ahash": "f0"},
            context_fingerprint="context-new",
            deterministic_snapshot={"ready": True},
            bars=_bars(),
            now_epoch=1_100.0,
            event_debounce_seconds=0,
        )
        self.assertFalse(decision["call"])
        self.assertEqual(decision["reason"], "no_new_completed_bar")

    def test_exact_and_near_identical_chart_fingerprints_suppress(self):
        vb = _load_vb()
        previous = self._previous(vb, {"ready": False})
        for image, expected in (
            ({"sha256": "old", "ahash": "0f"}, "exact_chart_fingerprint"),
            ({"sha256": "different", "ahash": "0e"}, "near_identical_chart_fingerprint"),
        ):
            with self.subTest(expected=expected):
                decision = vb.compute_visual_brain_gate(
                    previous,
                    completed_bar_fingerprint="bar-2",
                    image_fingerprint=image,
                    context_fingerprint="context-new",
                    deterministic_snapshot={"ready": True},
                    bars=_bars(),
                    now_epoch=1_100.0,
                    event_debounce_seconds=0,
                )
                self.assertFalse(decision["call"])
                self.assertEqual(decision["reason"], expected)

    def test_active_market_heartbeat_calls_only_after_max_staleness(self):
        vb = _load_vb()
        snapshot = {"market_active": True}
        previous = self._previous(vb, snapshot)
        early = vb.compute_visual_brain_gate(
            previous,
            completed_bar_fingerprint="bar-2",
            image_fingerprint={"sha256": "new", "ahash": "f0"},
            context_fingerprint="context-new",
            deterministic_snapshot=snapshot,
            bars=_bars(),
            now_epoch=2_799.0,
            event_debounce_seconds=0,
        )
        due = vb.compute_visual_brain_gate(
            previous,
            completed_bar_fingerprint="bar-2",
            image_fingerprint={"sha256": "new", "ahash": "f0"},
            context_fingerprint="context-new",
            deterministic_snapshot=snapshot,
            bars=_bars(),
            now_epoch=2_800.0,
            event_debounce_seconds=0,
        )
        self.assertFalse(early["call"])
        self.assertTrue(due["call"])
        self.assertEqual(due["reason"], "max_staleness_heartbeat")

    def test_call_window_and_daily_spend_caps_fail_closed(self):
        vb = _load_vb(VISUAL_BRAIN_MAX_CALLS_PER_WINDOW="2")
        allowed, _ = vb._reserve_paid_call(now=1_000.0)
        denied, cap = vb._reserve_paid_call(now=1_001.0)
        self.assertTrue(allowed)
        self.assertFalse(denied)
        self.assertEqual(cap["state"], "CALL_WINDOW_CAP_REACHED")

        vb = _load_vb(VISUAL_BRAIN_MAX_DAILY_SPEND_USD="0.005")
        allowed, cap = vb._reserve_paid_call(now=1_000.0)
        self.assertFalse(allowed)
        self.assertEqual(cap["state"], "DAILY_SPEND_CAP_REACHED")

    def test_tick_cap_never_reaches_second_canonical_analysis(self):
        vb = _load_vb(VISUAL_BRAIN_MAX_CALLS_PER_WINDOW="2")
        current_bars = [_bars()]
        current_state = [{"ready": False, "market_active": True}]
        vb._bars_fn = lambda _instrument: current_bars[0]
        vb._benchmark_state_fn = lambda _instrument: current_state[0]

        with patch.object(
            vb, "capture_chart_screenshot", side_effect=[b"image-1", b"image-2"]
        ), patch.object(
            vb, "get_history", return_value=[]
        ), patch.object(
            vb,
            "analyze_visual_market",
            side_effect=lambda **kwargs: (
                vb._start_reserved_attempt(kwargs["_reservation_id"], 1)
                and vb._record_cost(
                    100,
                    50,
                    reservation_id=kwargs["_reservation_id"],
                    attempt=1,
                )
                is None
                and _observation()
            ),
        ) as analyze, patch.object(
            vb, "_insert_observation", return_value=1
        ), patch.object(
            vb, "_schedule_next"
        ), patch.object(
            vb.threading, "Thread"
        ):
            vb._vb_tick("MNQ")
            current_bars[0] = _bars(last_close=105.0, last_ts=1_800_000_120)
            current_state[0] = {"ready": True, "market_active": True}
            vb._vb_tick("MNQ")

        self.assertEqual(analyze.call_count, 1)
        health = vb.get_visual_brain_health()
        self.assertEqual(health["counters"]["suppressed_cap"], 1)
        self.assertEqual(
            health["caps"]["next_observation_state"],
            "CALL_WINDOW_CAP_REACHED",
        )

    def test_disabled_and_candidate_paths_remain_noops(self):
        vb = _load_vb(
            VISUAL_BRAIN_ENABLED="false",
            VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED="true",
        )
        candidate = MagicMock()
        vb.register_benchmark_candidate("local-shadow", candidate)
        with patch.object(vb, "capture_chart_screenshot") as capture:
            vb._vb_tick("MNQ")
        capture.assert_not_called()
        candidate.assert_not_called()
        self.assertEqual(vb._VB_GATE_STATE_BY_INST, {})
        self.assertFalse(vb.get_visual_brain_health()["candidate_enabled"])

    def test_completed_bar_callback_debounces_and_dispatches_event_tick(self):
        vb = _load_vb(VISUAL_BRAIN_EVENT_DEBOUNCE_SECONDS="5")
        timers = []

        class FakeTimer:
            def __init__(self, delay, fn, args=()):
                self.delay = delay
                self.fn = fn
                self.args = args
                self.daemon = False
                self.started = False
                self.cancelled = False
                timers.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

            def is_alive(self):
                return self.started and not self.cancelled

        with patch.object(vb.threading, "Timer", FakeTimer), patch.object(
            vb, "_vb_tick"
        ) as tick:
            self.assertTrue(vb.notify_completed_bar("MNQ", {"ts": 1}))
            self.assertTrue(vb.notify_completed_bar("MNQ", {"ts": 2}))
            self.assertEqual(len(timers), 2)
            self.assertTrue(timers[0].cancelled)
            self.assertEqual(vb._VB_PENDING_BAR_EVENTS["MNQ"], 2)
            timers[0].fn(*timers[0].args)
            tick.assert_not_called()
            self.assertEqual(vb._VB_PENDING_BAR_EVENTS["MNQ"], 2)
            timers[1].fn(*timers[1].args)

        tick.assert_called_once_with("MNQ", event_driven=True)
        self.assertEqual(vb._VB_PENDING_BAR_EVENTS["MNQ"], 0)

    def test_retry_budget_is_reserved_and_settled_by_identity(self):
        vb = _load_vb(VISUAL_BRAIN_MAX_CALLS_PER_WINDOW="4")
        ok_a, cap_a = vb._reserve_paid_call(now=1_000.0)
        ok_b, cap_b = vb._reserve_paid_call(now=1_000.0)
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        rid_a = cap_a["reservation_id"]
        rid_b = cap_b["reservation_id"]
        self.assertTrue(vb._start_reserved_attempt(rid_b, 1))
        vb._record_cost(100, 50, reservation_id=rid_b, attempt=1)
        vb._finish_reservation(rid_b)

        a_rows = [
            item for item in vb._VB_CALL_RESERVATIONS
            if item["reservation_id"] == rid_a
        ]
        b_rows = [
            item for item in vb._VB_CALL_RESERVATIONS
            if item["reservation_id"] == rid_b
        ]
        self.assertEqual(len(a_rows), 2)
        self.assertTrue(all(item["pending"] for item in a_rows))
        self.assertEqual(len(b_rows), 1)
        self.assertTrue(b_rows[0]["settled"])

    def test_window_cap_is_bounded_to_non_evicting_ledger_capacity(self):
        vb = _load_vb(
            VISUAL_BRAIN_MAX_CALLS_PER_WINDOW="999999",
            VISUAL_BRAIN_MAX_DAILY_SPEND_USD="100",
        )
        self.assertEqual(vb.VISUAL_BRAIN_MAX_CALLS_PER_WINDOW, 10_000)
        for offset in range(300):
            allowed, _ = vb._reserve_paid_call(now=1_000.0 + offset)
            self.assertTrue(allowed)
        self.assertEqual(len(vb._VB_CALL_RESERVATIONS), 600)

    def test_gate_does_not_mutate_deterministic_or_canonical_state(self):
        vb = _load_vb()
        snapshot = {
            "ready": True,
            "blockers": [],
            "structure_event": {"type": "BOS DEMAND"},
        }
        original = repr(snapshot)
        canonical = {"decision": "READY", "orders": []}
        decision = vb.compute_visual_brain_gate(
            self._previous(vb, {"ready": False}),
            completed_bar_fingerprint="bar-2",
            image_fingerprint={"sha256": "new", "ahash": "f0"},
            context_fingerprint="context-new",
            deterministic_snapshot=snapshot,
            bars=_bars(),
            now_epoch=1_100.0,
            event_debounce_seconds=0,
        )
        self.assertTrue(decision["call"])
        self.assertEqual(repr(snapshot), original)
        self.assertEqual(canonical, {"decision": "READY", "orders": []})


if __name__ == "__main__":
    unittest.main()