"""Focused tests for the shadow-only Visual Brain cost benchmark."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


def _load_vb(*, benchmark: bool, candidate: bool = False) -> types.ModuleType:
    sys.modules.pop("visual_brain", None)
    env = {
        "VISUAL_BRAIN_ENABLED": "true",
        "VISUAL_BRAIN_BENCHMARK_ENABLED": "true" if benchmark else "false",
        "VISUAL_BRAIN_BENCHMARK_CANDIDATE_ENABLED": "true" if candidate else "false",
        "VISUAL_BRAIN_INTERVAL_SECONDS": "300",
        "AI_INTEGRATIONS_OPENAI_API_KEY": "test-key",
        "AI_INTEGRATIONS_OPENAI_BASE_URL": "https://api.openai.com/v1",
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


def _mode_assessments() -> dict:
    return {
        "scalp": {
            "posture": "LONG_BIAS",
            "setup_status": "FORMING",
            "confidence": 70,
            "validation": "Hold VWAP",
            "invalidation": "Lose VWAP",
            "reason": "Structure is constructive.",
        },
        "intraday_trend": {
            "posture": "LONG_BIAS",
            "setup_status": "FORMING",
            "confidence": 65,
            "timeframe_alignment": "MIXED",
            "market_phase": "PULLBACK",
            "session_level": "VWAP",
            "validation": "Reclaim the high",
            "invalidation": "Break the low",
            "reason": "Trend is intact.",
        },
        "swing": {
            "posture": "NEUTRAL",
            "setup_status": "WAIT",
            "confidence": 40,
            "timeframe_alignment": "UNKNOWN",
            "thesis_quality": "UNKNOWN",
            "structural_stop": "UNKNOWN",
            "target_context": "UNKNOWN",
            "validation": "Wait for alignment",
            "invalidation": "No thesis yet",
            "reason": "Insufficient higher-timeframe evidence.",
        },
    }


def _observation() -> dict:
    return {
        "instrument": "MNQ",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bias": "BULLISH",
        "market_state": "TRENDING_UP",
        "structure": {
            "short_term": "HH_HL",
            "higher_low_intact": True,
            "lower_high_intact": False,
        },
        "last_event": "RECLAIM",
        "support": {"visible": True, "description": "VWAP", "approx_price": 21000.0},
        "resistance": {"visible": True, "description": "High", "approx_price": 21100.0},
        "long_condition": "Hold above VWAP",
        "short_condition": "Lose VWAP",
        "action": "LONG_WATCH",
        "confidence": 75,
        "state_changed": True,
        "state_change_reason": "VWAP reclaim",
        "summary": "Bullish structure above VWAP.",
        "mode_assessments": _mode_assessments(),
    }


def _bars() -> list[dict]:
    return [
        {
            "ts": 1_800_000_000 + index * 60,
            "open": 21000.0 + index,
            "high": 21005.0 + index,
            "low": 20995.0 + index,
            "close": 21002.0 + index,
            "volume": 100 + index,
        }
        for index in range(8)
    ]


class VisualBrainBenchmarkTests(unittest.TestCase):
    def tearDown(self) -> None:
        module = sys.modules.get("visual_brain")
        if module is not None:
            for timer in getattr(module, "_VB_TIMERS", {}).values():
                try:
                    timer.cancel()
                except Exception:
                    pass
        sys.modules.pop("visual_brain", None)

    def test_benchmark_disabled_preserves_tick_and_records_nothing(self):
        vb = _load_vb(benchmark=False, candidate=True)
        vb._bars_fn = lambda _instrument: _bars()
        canonical = _observation()
        candidate_runner = MagicMock(side_effect=AssertionError("must remain disabled"))
        vb.register_benchmark_candidate("openai-cheap", candidate_runner)

        with patch.object(vb, "capture_chart_screenshot", return_value=b"same-jpeg"), \
             patch.object(vb, "get_history", return_value=[]), \
             patch.object(vb, "analyze_visual_market", return_value=canonical) as analyze, \
             patch.object(vb, "_insert_observation", return_value=7) as insert, \
             patch.object(vb, "_schedule_next"), \
             patch.object(vb.threading, "Thread"):
            vb._vb_tick("MNQ")

        analyze.assert_called_once()
        insert.assert_called_once()
        candidate_runner.assert_not_called()
        self.assertEqual(vb.get_benchmark_report()["counters"]["cycles"], 0)
        self.assertEqual(
            vb._LAST_OBSERVATION_BY_INST["MNQ"]["action"], "LONG_WATCH"
        )

    def test_no_new_bar_and_near_identical_image_are_telemetry_only(self):
        vb = _load_vb(benchmark=True)
        previous = {
            "completed_bar_fingerprint": "bar-1",
            "image_fingerprint": {"sha256": "old", "ahash": "0f"},
            "semantic_snapshot": vb._benchmark_semantic_snapshot(
                {"ready": False, "vwap": {"value": 21000.0}}
            ),
            "last_baseline_at": 1_000.0,
        }
        snapshot = {"ready": False, "vwap": {"value": 21000.0}}
        before = json.dumps(snapshot, sort_keys=True)

        result = vb.compute_benchmark_trigger_policies(
            previous,
            completed_bar_fingerprint="bar-1",
            image_fingerprint={"sha256": "new", "ahash": "0e"},
            deterministic_snapshot=snapshot,
            now_epoch=1_100.0,
            max_staleness_seconds=1_800,
        )

        image_policy = result["no_new_bar_image"]
        self.assertFalse(image_policy["would_call"])
        self.assertIn("no_new_completed_bar", image_policy["reasons"])
        self.assertTrue(image_policy["near_identical_image"])
        self.assertFalse(result["deterministic_events"]["would_call"])
        self.assertFalse(result["max_staleness_heartbeat"]["would_call"])
        self.assertEqual(json.dumps(snapshot, sort_keys=True), before)
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST, {})

    def test_meaningful_event_and_heartbeat_are_reported_without_gating(self):
        vb = _load_vb(benchmark=True)
        previous = {
            "completed_bar_fingerprint": "bar-1",
            "image_fingerprint": {"sha256": "old", "ahash": "0f"},
            "semantic_snapshot": vb._benchmark_semantic_snapshot({
                "ready": False,
                "vwap": {"value": 21000.0},
                "structure_event": {"type": "CHOCH DEMAND"},
            }),
            "last_baseline_at": 1_000.0,
        }
        result = vb.compute_benchmark_trigger_policies(
            previous,
            completed_bar_fingerprint="bar-2",
            image_fingerprint={"sha256": "changed", "ahash": "f0"},
            deterministic_snapshot={
                "ready": True,
                "blockers": [],
                "vwap": {"value": 21010.0},
                "structure_event": {"type": "BOS DEMAND"},
                "volatility": {"ratio": 1.8},
                "volume": {"rvol": 2.1},
                "session": "NY_OPEN",
                "recovery_events": ["databento_recovered"],
            },
            now_epoch=2_901.0,
            max_staleness_seconds=1_800,
        )

        self.assertTrue(result["no_new_bar_image"]["would_call"])
        self.assertTrue(result["deterministic_events"]["would_call"])
        reasons = " ".join(result["deterministic_events"]["reasons"])
        self.assertIn("structure/BOS/CHOCH", reasons)
        self.assertIn("thesis/READY/blocker", reasons)
        self.assertIn("recovery", reasons)
        self.assertTrue(result["max_staleness_heartbeat"]["would_call"])
        self.assertTrue(result["max_staleness_heartbeat"]["heartbeat_used"])

    def test_metrics_do_not_enter_canonical_observation_or_persistence(self):
        vb = _load_vb(benchmark=True)
        bars = _bars()
        vb.start(
            bars_fn=lambda _instrument: bars,
            benchmark_state_fn=lambda _instrument: {
                "structure_event": {"type": "BOS DEMAND"},
                "ready": True,
                "blockers": [],
            },
        )
        canonical = _observation()
        inserted = []

        with patch.object(vb, "capture_chart_screenshot", return_value=b"jpeg"), \
             patch.object(vb, "get_history", return_value=[]), \
             patch.object(vb, "analyze_visual_market", return_value=canonical), \
             patch.object(vb, "_insert_observation",
                          side_effect=lambda obs, screenshot_path=None: inserted.append(dict(obs)) or 8), \
             patch.object(vb, "_schedule_next"), \
             patch.object(vb.threading, "Thread"):
            vb._vb_tick("MNQ")

        self.assertEqual(len(inserted), 1)
        self.assertNotIn("benchmark", inserted[0])
        self.assertNotIn("benchmark", vb._LAST_OBSERVATION_BY_INST["MNQ"])
        self.assertEqual(inserted[0]["action"], canonical["action"])
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MNQ"]["bias"], canonical["bias"])
        report = vb.get_benchmark_report()
        self.assertEqual(report["counters"]["cycles"], 1)
        self.assertEqual(report["recent_cycles"][0]["instrument"], "MNQ")

    def test_candidate_failure_is_fail_open_and_runs_after_baseline_persist(self):
        vb = _load_vb(benchmark=True, candidate=True)
        vb._bars_fn = lambda _instrument: _bars()
        canonical = _observation()
        order = []
        candidate_ran = threading.Event()

        def failing_candidate(_payload):
            order.append("candidate")
            candidate_ran.set()
            raise RuntimeError("candidate unavailable")

        vb.register_benchmark_candidate("openai-cheap", failing_candidate)
        with patch.object(vb, "capture_chart_screenshot", return_value=b"jpeg"), \
             patch.object(vb, "get_history", return_value=[]), \
             patch.object(vb, "analyze_visual_market", return_value=canonical), \
             patch.object(vb, "_insert_observation",
                          side_effect=lambda *_args, **_kwargs: order.append("persist") or 9), \
             patch.object(vb, "_schedule_next"):
            vb._vb_tick("MNQ")

        self.assertTrue(candidate_ran.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        while (
            vb.get_benchmark_report()["counters"]["candidate_failures"] < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(order, ["persist", "candidate"])
        self.assertEqual(vb._LAST_OBSERVATION_BY_INST["MNQ"]["action"], "LONG_WATCH")
        counters = vb.get_benchmark_report()["counters"]
        self.assertEqual(counters["candidate_calls"], 1)
        self.assertEqual(counters["candidate_failures"], 1)
        self.assertEqual(counters["candidate_errors"]["RuntimeError"], 1)

    def test_hung_candidate_cannot_delay_reschedule_or_tick_completion(self):
        vb = _load_vb(benchmark=True, candidate=True)
        vb._bars_fn = lambda _instrument: _bars()
        entered = threading.Event()
        release = threading.Event()

        def hanging_candidate(_payload):
            entered.set()
            release.wait(timeout=2.0)
            return {"schema_valid": True, "response_received": True}

        vb.register_benchmark_candidate("openai-cheap", hanging_candidate)
        started = time.monotonic()
        try:
            with patch.object(vb, "capture_chart_screenshot", return_value=b"jpeg"), \
                 patch.object(vb, "get_history", return_value=[]), \
                 patch.object(vb, "analyze_visual_market", return_value=_observation()), \
                 patch.object(vb, "_insert_observation", return_value=10), \
                 patch.object(vb, "_schedule_next") as schedule:
                vb._vb_tick("MNQ")
                self.assertTrue(entered.wait(timeout=0.5))
                for _ in range(4):
                    vb._vb_tick("MNQ")
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.5)
            self.assertEqual(schedule.call_count, 5)
            schedule.assert_called_with("MNQ")
            report = vb.get_benchmark_report()
            self.assertEqual(report["counters"]["candidate_scheduled"], 1)
            self.assertEqual(report["counters"]["candidate_skipped_busy"], 4)
            self.assertEqual(report["counters"]["candidate_calls"], 0)
            self.assertEqual(vb._BENCHMARK_ACTIVE_CANDIDATES, {"MNQ"})
            self.assertEqual(len(vb._BENCHMARK_PENDING_CANDIDATES), 0)
        finally:
            release.set()

    def test_candidate_uses_independent_configured_pricing(self):
        with patch.dict(os.environ, {
            "VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION": "1.25",
            "VISUAL_BRAIN_BENCHMARK_CANDIDATE_OUTPUT_COST_PER_MILLION": "5.00",
        }):
            vb = _load_vb(benchmark=True, candidate=True)
        candidate = vb._normalize_candidate_result("local", {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "schema_valid": True,
            "response_received": True,
        })
        self.assertEqual(candidate["estimated_cost_usd"], 6.25)
        self.assertNotEqual(
            vb.VISUAL_BRAIN_BENCHMARK_CANDIDATE_INPUT_COST_PER_MILLION,
            vb._COST_PER_INPUT_TOK,
        )

    def test_late_candidate_result_is_counted_after_cycle_record_eviction(self):
        vb = _load_vb(benchmark=True, candidate=True)
        vb._bars_fn = lambda _instrument: _bars()
        entered = threading.Event()
        release = threading.Event()

        def slow_candidate(_payload):
            entered.set()
            release.wait(timeout=2.0)
            return {
                "input_tokens": 20,
                "output_tokens": 5,
                "schema_valid": True,
                "response_received": True,
            }

        vb.register_benchmark_candidate("openai-cheap", slow_candidate)
        with patch.object(vb, "capture_chart_screenshot", return_value=b"jpeg"), \
             patch.object(vb, "get_history", return_value=[]), \
             patch.object(vb, "analyze_visual_market", return_value=_observation()), \
             patch.object(vb, "_insert_observation", return_value=11), \
             patch.object(vb, "_schedule_next"):
            vb._vb_tick("MNQ")
        self.assertTrue(entered.wait(timeout=0.5))
        with vb._BENCHMARK_LOCK:
            for index in range(vb._BENCHMARK_HISTORY_LIMIT):
                vb._BENCHMARK_RECENT.append({"cycle_id": f"replacement:{index}"})
        release.set()

        deadline = time.monotonic() + 1.0
        while (
            vb.get_benchmark_report()["counters"]["candidate_calls"] < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        counters = vb.get_benchmark_report()["counters"]
        self.assertEqual(counters["candidate_calls"], 1)
        self.assertEqual(counters["candidate_successes"], 1)
        self.assertEqual(counters["candidate_input_tokens"], 20)
        self.assertEqual(counters["candidate_output_tokens"], 5)
        self.assertEqual(counters["candidate_late_or_evicted_results"], 1)
        self.assertEqual(len(vb._BENCHMARK_PENDING_CANDIDATES), 0)

    def test_avoided_call_projection_counts_observed_retry_calls(self):
        vb = _load_vb(benchmark=True)
        cycle = vb._benchmark_begin_cycle("MNQ")
        cycle["policies"] = {
            name: {"would_call": False, "reasons": ["unchanged"]}
            for name in (
                "no_new_bar_image",
                "deterministic_events",
                "max_staleness_heartbeat",
            )
        }
        cycle["baseline_attempts"] = [
            {"attempt": 1, "ok": False},
            {"attempt": 2, "ok": True},
        ]
        vb._benchmark_finish_cycle(cycle)
        projected = vb.get_benchmark_report()["counters"]["projected"]
        self.assertEqual(projected["no_new_bar_image"]["avoided_calls"], 2)
        self.assertEqual(projected["deterministic_events"]["avoided_calls"], 2)
        self.assertEqual(projected["max_staleness_heartbeat"]["avoided_calls"], 2)

    def test_unchanged_recovery_state_is_not_a_new_event(self):
        vb = _load_vb(benchmark=True)
        state = {
            "recovery": {
                "price_available": True,
                "vwap_available": True,
                "volatility_available": True,
            },
        }
        previous = {
            "semantic_snapshot": vb._benchmark_semantic_snapshot(state),
            "last_event_signature": vb._stable_hash({
                "changed_families": [],
                "reasons": [],
            }),
            "last_baseline_at": 100.0,
        }
        result = vb.compute_benchmark_trigger_policies(
            previous,
            completed_bar_fingerprint="bar",
            image_fingerprint={"sha256": "image", "ahash": "a"},
            deterministic_snapshot=state,
            now_epoch=101.0,
        )
        self.assertFalse(result["deterministic_events"]["would_call"])
        self.assertNotIn(
            "recovery event", result["deterministic_events"]["reasons"]
        )

    def test_duplicate_start_does_not_create_duplicate_timers(self):
        vb = _load_vb(benchmark=False)
        fake_timer = MagicMock()
        fake_timer.is_alive.return_value = True
        with patch.object(vb.threading, "Timer", return_value=fake_timer) as timer:
            vb.start()
            first_call_count = timer.call_count
            vb.start()
        self.assertEqual(first_call_count, len(vb._VB_SYMBOLS))
        self.assertEqual(timer.call_count, first_call_count)

    def test_baseline_retry_schema_failure_and_usage_are_measured(self):
        vb = _load_vb(benchmark=True)
        cycle = vb._benchmark_begin_cycle("MNQ")
        vb._benchmark_prepare_cycle(
            cycle,
            bars=_bars(),
            screenshot_bytes=b"jpeg",
            market_context={"bias": "BULLISH"},
            previous_state=None,
            recent_history=[],
        )
        invalid = MagicMock()
        invalid.choices = [MagicMock()]
        invalid.choices[0].message.content = "{}"
        invalid.usage.prompt_tokens = 90
        invalid.usage.completion_tokens = 10
        valid = MagicMock()
        valid.choices = [MagicMock()]
        valid.choices[0].message.content = json.dumps(_observation())
        valid.usage.prompt_tokens = 100
        valid.usage.completion_tokens = 50

        vb._BENCHMARK_LOCAL.cycle = cycle
        try:
            with patch("openai.OpenAI") as openai_client, \
                 patch.object(vb.time, "sleep"):
                openai_client.return_value.chat.completions.create.side_effect = [
                    invalid, valid,
                ]
                result = vb.analyze_visual_market(
                    b"jpeg", None, [], "MNQ", {"bias": "BULLISH"}
                )
        finally:
            vb._BENCHMARK_LOCAL.cycle = None
        vb._benchmark_finish_cycle(cycle)

        self.assertEqual(result["action"], "LONG_WATCH")
        counters = vb.get_benchmark_report()["counters"]
        self.assertEqual(counters["baseline_api_calls"], 2)
        self.assertEqual(counters["baseline_retries"], 1)
        self.assertEqual(counters["baseline_schema_failures"], 1)
        self.assertEqual(counters["baseline_input_tokens"], 190)
        self.assertEqual(counters["baseline_output_tokens"], 60)

    def test_disabled_benchmark_keeps_real_baseline_request_contract(self):
        vb = _load_vb(benchmark=False)
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(_observation())
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50

        with patch("openai.OpenAI") as openai_client:
            create = openai_client.return_value.chat.completions.create
            create.return_value = response
            result = vb.analyze_visual_market(
                b"jpeg", None, [], "MNQ", {"bias": "BULLISH"}
            )

        create.assert_called_once()
        request = create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-5.4")
        self.assertEqual(request["max_completion_tokens"], vb._VB_MAX_TOKENS)
        self.assertEqual(request["messages"][0], {
            "role": "system",
            "content": vb._get_system_prompt("MNQ"),
        })
        self.assertEqual(request["messages"][1]["role"], "user")
        self.assertEqual(
            request["messages"][1]["content"][1]["image_url"]["detail"], "auto"
        )
        self.assertEqual(result["action"], "LONG_WATCH")
        self.assertEqual(vb.get_benchmark_report()["counters"]["cycles"], 0)

    def test_read_only_route_and_proxy_are_registered(self):
        app_source = (ROOT / "app.py").read_text()
        proxy_source = (
            ROOT.parent / "api-server" / "src" / "routes" / "flask-proxy.ts"
        ).read_text()
        self.assertIn('@app.route("/visual-brain/benchmark", methods=["GET"])', app_source)
        self.assertIn('"/visual-brain/benchmark"', proxy_source)


if __name__ == "__main__":
    unittest.main()