"""Focused regression tests for the final broker-transmission safety boundary.

All broker requests are mocked. These tests assert the boundary itself rather than
only recording checklist failures, so a new direct broker route cannot bypass it.
"""

import ast
import os
import sys
import time
import unittest
from contextlib import ExitStack
from unittest import mock

os.environ.setdefault("TRADING_MODE", "SCALP")
os.environ.setdefault("EXECUTION_MODE", "paper")
os.environ.setdefault("SESSION_SECRET", "test-final-order-boundary")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-final-order-boundary")
os.environ.setdefault("DATABASE_URL", "")

sys.path.insert(0, os.path.dirname(__file__))
import app  # noqa: E402


class _FakeResponse:
    status_code = 200
    text = "ok"


class TestFinalOrderSafetyBoundary(unittest.TestCase):
    def setUp(self):
        self._orig_last = dict(app._TRADERSPOST_LAST)
        self._orig_trade = app.active_trade_for("MGC")
        with app._BROKER_SIDE_LOCK:
            self._orig_side = app._BROKER_LAST_SIDE.get("MGC")

    def tearDown(self):
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST.clear()
            app._TRADERSPOST_LAST.update(self._orig_last)
        with app.ACTIVE_TRADES_LOCK:
            if self._orig_trade is None:
                app.ACTIVE_TRADES_BY_INST.pop("MGC", None)
            else:
                app.ACTIVE_TRADES_BY_INST["MGC"] = self._orig_trade
        with app._BROKER_SIDE_LOCK:
            if self._orig_side is None:
                app._BROKER_LAST_SIDE.pop("MGC", None)
            else:
                app._BROKER_LAST_SIDE["MGC"] = self._orig_side

    @staticmethod
    def _payload():
        return {
            "ticker": app.TRADERSPOST_TICKER.get("MGC", "MGC"), "action": "buy", "quantity": 1,
            "sentiment": "long",
            "stopLoss": {"type": "stop", "stopPrice": 2492.0},
            "takeProfit": {"limitPrice": 2508.0},
        }

    def _context(self, **overrides):
        epoch = time.time()
        fingerprint = "test-final-boundary"
        with app._TRADERSPOST_LOCK:
            app._TRADERSPOST_LAST["MGC"] = (fingerprint, epoch)
        context = {
            "schema": "final_order_v1",
            "mode": "traderspost",
            "instrument": "MGC",
            "source": "auto",
            "provenance": "autonomous",
            "intent_kind": "entry",
            "direction": "Long",
            "quantity": 1,
            "strategy": "test_strategy",
            "market_open": True,
            "analysis_snapshot": {"_final_boundary_at": app.now_utc().isoformat()},
            "bracket": {"entry": 2500.0, "stop": 2492.0, "target1": 2508.0},
            "bracket_kind": "full",
            "dedupe_reservation": {"fingerprint": fingerprint, "epoch": epoch},
        }
        context.update(overrides)
        return context

    def _run_boundary(self, context, payload=None, arm_result=(True, "armed", {})):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "emergency_disabled", return_value=False))
            stack.enter_context(mock.patch.object(app, "max_losses_per_day", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_daily_loss", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_open_trades", return_value=None))
            stack.enter_context(mock.patch.object(app, "_check_arm_for_transmission",
                                                   return_value=arm_result))
            return app._final_order_safety_check(
                "traderspost", "MGC", payload or self._payload(), context, "entry")

    def test_missing_context_blocks_without_broker_post(self):
        with mock.patch("requests.post") as post, \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", safety_context=None)
        self.assertEqual(code, 400)
        self.assertEqual(result["final_boundary"], "blocked")
        self.assertIn("missing typed safety context", result["reason"])
        post.assert_not_called()

    def test_stale_candidate_blocks_at_final_boundary(self):
        old = app.now_utc().replace(microsecond=0)
        stale = (old.timestamp() - 3600)
        context = self._context()
        context["analysis_snapshot"]["generated_at"] = app.datetime.fromtimestamp(
            stale, tz=app.timezone.utc).isoformat()
        result, code = self._run_boundary(context)
        self.assertEqual(code, 409)
        self.assertIn("stale candidate", result["reason"])

    def test_bracket_mismatch_blocks_before_broker_post(self):
        payload = self._payload()
        payload["takeProfit"]["limitPrice"] = 2509.0
        result, code = self._run_boundary(self._context(), payload)
        self.assertEqual(code, 400)
        self.assertIn("target differs", result["reason"])

    def test_provider_symbol_mismatch_blocks_before_broker_post(self):
        payload = self._payload()
        payload["ticker"] = "MNQ1!"
        result, code = self._run_boundary(self._context(), payload)
        self.assertEqual(code, 400)
        self.assertIn("provider symbol differs", result["reason"])

    def test_unknown_market_session_or_missing_freshness_anchor_blocks(self):
        unknown_session = self._context(market_open=None)
        result, code = self._run_boundary(unknown_session)
        self.assertEqual(code, 409)
        self.assertIn("session is not explicitly open", result["reason"])

        missing_anchor = self._context(analysis_snapshot={})
        result, code = self._run_boundary(missing_anchor)
        self.assertEqual(code, 409)
        self.assertIn("missing server freshness anchor", result["reason"])

    def test_missing_or_changed_dedupe_reservation_blocks(self):
        context = self._context()
        context["dedupe_reservation"]["fingerprint"] = "different"
        result, code = self._run_boundary(context)
        self.assertEqual(code, 409)
        self.assertIn("idempotency reservation", result["reason"])

    def test_disarm_race_blocks_before_transmission(self):
        result, code = self._run_boundary(
            self._context(), arm_result=(False, "DISARMED", {"armed": False}))
        self.assertEqual(code, 409)
        self.assertEqual(result["reason_code"], "DISARMED")
        self.assertIn("No order was sent", result["reason"])

    def test_valid_context_reaches_one_mocked_broker_post(self):
        context = self._context()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "emergency_disabled", return_value=False))
            stack.enter_context(mock.patch.object(app, "max_losses_per_day", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_daily_loss", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_open_trades", return_value=None))
            stack.enter_context(mock.patch.object(app, "_check_arm_for_transmission",
                                                   return_value=(True, "armed", {})))
            post = stack.enter_context(mock.patch("requests.post", return_value=_FakeResponse()))
            stack.enter_context(mock.patch.object(app, "_record_broker_send"))
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", safety_context=context)
        self.assertIsNone(result)
        self.assertIsNone(code)
        post.assert_called_once()

    def test_exit_requires_exact_active_position_identity(self):
        opened_at = "2026-08-23T12:00:00+00:00"
        exit_context = {
            "schema": "final_order_v1", "mode": "traderspost",
            "instrument": "MGC", "source": "quick_exit",
            "provenance": "operator", "intent_kind": "exit",
            "position_identity": {"kind": "active_trade", "opened_at": opened_at},
        }
        result, code = app._final_order_safety_check(
            "traderspost", "MGC",
            {"ticker": app.TRADERSPOST_TICKER.get("MGC", "MGC"), "action": "exit"},
            exit_context, "quick_exit")
        self.assertEqual(code, 409)
        self.assertIn("tracked position changed", result["reason"])

        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST["MGC"] = {"opened_at": opened_at, "direction": "Long"}
        result, code = app._final_order_safety_check(
            "traderspost", "MGC",
            {"ticker": app.TRADERSPOST_TICKER.get("MGC", "MGC"), "action": "exit"},
            exit_context, "quick_exit")
        self.assertIsNone(result)
        self.assertIsNone(code)

    def test_exit_symbol_mismatch_blocks_even_when_position_matches(self):
        opened_at = "2026-08-23T12:00:00+00:00"
        with app.ACTIVE_TRADES_LOCK:
            app.ACTIVE_TRADES_BY_INST["MGC"] = {"opened_at": opened_at, "direction": "Long"}
        exit_context = {
            "schema": "final_order_v1", "mode": "traderspost",
            "instrument": "MGC", "source": "quick_exit",
            "provenance": "operator", "intent_kind": "exit",
            "position_identity": {"kind": "active_trade", "opened_at": opened_at},
        }
        result, code = app._final_order_safety_check(
            "traderspost", "MGC", {"ticker": "MNQ1!", "action": "exit"},
            exit_context, "quick_exit")
        self.assertEqual(code, 400)
        self.assertIn("provider symbol differs", result["reason"])

    def test_quick_exit_posts_provider_correct_non_reversing_payload(self):
        """The owner quick-exit route must work in every supported live provider."""
        opened_at = "2026-08-23T12:00:00+00:00"
        expected_symbol = app.TRADERSPOST_TICKER.get("MGC", "MGC")
        for mode, expected_fields in (
            ("traderspost", {"ticker": expected_symbol, "action": "exit"}),
            ("pickmytrade", {"symbol": expected_symbol, "data": "close"}),
        ):
            with self.subTest(mode=mode):
                with app.ACTIVE_TRADES_LOCK:
                    app.ACTIVE_TRADES_BY_INST["MGC"] = {
                        "opened_at": opened_at, "direction": "Long",
                        "entry_price": 2500.0, "symbol": "MGC",
                    }
                with ExitStack() as stack:
                    stack.enter_context(mock.patch.object(app, "resolve_execution_mode",
                                                           return_value=mode))
                    stack.enter_context(mock.patch.object(app, "execution_is_live",
                                                           return_value=True))
                    stack.enter_context(mock.patch.object(app, "TRADERSPOST_WEBHOOK_URL",
                                                           "https://traderspost.invalid"))
                    stack.enter_context(mock.patch.object(app, "EXECUTION_WEBHOOK_URL",
                                                           "https://pickmytrade.invalid"))
                    stack.enter_context(mock.patch.object(app, "display_price_for",
                                                           return_value=(None, None)))
                    stack.enter_context(mock.patch.object(app, "clear_active_trade",
                                                           return_value={}))
                    stack.enter_context(mock.patch.object(app, "_update_journal_outcome"))
                    stack.enter_context(mock.patch.object(app, "_discord_url", return_value=""))
                    stack.enter_context(mock.patch.object(app, "_record_broker_send"))
                    post = stack.enter_context(mock.patch(
                        "requests.post", return_value=_FakeResponse()))
                    response = app.app.test_client().post(
                        "/quick-exit", json={"ticker": "MGC"})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()["status"], "sent")
                post.assert_called_once()
                sent_payload = post.call_args.kwargs["json"]
                for key, value in expected_fields.items():
                    self.assertEqual(sent_payload[key], value)
                self.assertNotIn(
                    "sell", (sent_payload.get("action") or sent_payload.get("data") or "").lower())

    def test_ambiguous_broker_response_holds_reservation(self):
        context = self._context()

        class _AmbiguousResponse:
            status_code = 503
            text = "unavailable"

        releases = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "emergency_disabled", return_value=False))
            stack.enter_context(mock.patch.object(app, "max_losses_per_day", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_daily_loss", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_open_trades", return_value=None))
            stack.enter_context(mock.patch.object(app, "_check_arm_for_transmission",
                                                   return_value=(True, "armed", {})))
            stack.enter_context(mock.patch("requests.post", return_value=_AmbiguousResponse()))
            stack.enter_context(mock.patch.object(app, "_record_broker_send"))
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", release_slot=lambda: releases.append(True),
                safety_context=context)
        self.assertEqual(code, 502)
        self.assertTrue(result["broker_verify_required"])
        self.assertEqual(releases, [])
        self.assertEqual(
            app._TRADERSPOST_LAST["MGC"],
            (context["dedupe_reservation"]["fingerprint"],
             context["dedupe_reservation"]["epoch"]),
        )

    def test_stale_anchor_never_becomes_fresh_after_reversal_wait(self):
        """A long wait may not renew caller-provided stale safety evidence."""
        context = self._context()
        context["analysis_snapshot"]["_final_boundary_at"] = app.datetime.fromtimestamp(
            time.time() - 120, tz=app.timezone.utc).isoformat()
        previous_side = ("sell", time.time())
        with app._BROKER_SIDE_LOCK:
            app._BROKER_LAST_SIDE["MGC"] = previous_side
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "BROKER_OPPOSITE_SIDE_BUFFER_SEC", 31.0))
            stack.enter_context(mock.patch("time.sleep"))
            stack.enter_context(mock.patch.object(app, "emergency_disabled", return_value=False))
            stack.enter_context(mock.patch.object(app, "max_losses_per_day", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_daily_loss", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_open_trades", return_value=None))
            stack.enter_context(mock.patch.object(app, "_check_arm_for_transmission",
                                                   return_value=(True, "armed", {})))
            post = stack.enter_context(mock.patch("requests.post"))
            stack.enter_context(mock.patch.object(app, "_record_exec_rejection"))
            stack.enter_context(mock.patch.object(app, "_record_diagnostic"))
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", safety_context=context)
        self.assertEqual(code, 409)
        self.assertIn("stale server freshness anchor", result["reason"])
        post.assert_not_called()

    def test_fresh_entry_can_complete_long_reversal_wait(self):
        """Freshness is checked before spacing; arm/session remain checked afterward."""
        context = self._context()
        with app._BROKER_SIDE_LOCK:
            app._BROKER_LAST_SIDE["MGC"] = ("sell", time.time())
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "BROKER_OPPOSITE_SIDE_BUFFER_SEC", 31.0))
            stack.enter_context(mock.patch("time.sleep"))
            stack.enter_context(mock.patch.object(app, "emergency_disabled", return_value=False))
            stack.enter_context(mock.patch.object(app, "max_losses_per_day", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_daily_loss", return_value=None))
            stack.enter_context(mock.patch.object(app, "max_open_trades", return_value=None))
            stack.enter_context(mock.patch.object(app, "_check_arm_for_transmission",
                                                   return_value=(True, "armed", {})))
            post = stack.enter_context(mock.patch("requests.post", return_value=_FakeResponse()))
            stack.enter_context(mock.patch.object(app, "_record_broker_send"))
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", safety_context=context)
        self.assertIsNone(result)
        self.assertIsNone(code)
        post.assert_called_once()

    def test_local_final_rejection_releases_provisional_reversal_reservation(self):
        context = self._context(market_open=False)
        previous_side = ("sell", time.time())
        with app._BROKER_SIDE_LOCK:
            app._BROKER_LAST_SIDE["MGC"] = previous_side
        with mock.patch.object(app, "BROKER_OPPOSITE_SIDE_BUFFER_SEC", 1.0), \
             mock.patch("time.sleep"), \
             mock.patch("requests.post") as post, \
             mock.patch.object(app, "_record_exec_rejection"), \
             mock.patch.object(app, "_record_diagnostic"):
            result, code = app._send_broker_order(
                "traderspost", "TradersPost", "MGC", self._payload(),
                "https://broker.invalid", safety_context=context)
        self.assertEqual(code, 409)
        self.assertIn("session is not explicitly open", result["reason"])
        post.assert_not_called()
        with app._BROKER_SIDE_LOCK:
            self.assertEqual(app._BROKER_LAST_SIDE["MGC"], previous_side)

    def test_sink_never_mints_missing_freshness_evidence_after_reversal_wait(self):
        """The real sink—not just the helper—must reject incomplete entry evidence."""
        for snapshot in ({}, None):
            with self.subTest(snapshot=snapshot):
                context = self._context(analysis_snapshot=snapshot)
                previous_side = ("sell", time.time())
                with app._BROKER_SIDE_LOCK:
                    app._BROKER_LAST_SIDE["MGC"] = previous_side
                with mock.patch.object(app, "BROKER_OPPOSITE_SIDE_BUFFER_SEC", 31.0), \
                     mock.patch("time.sleep"), \
                     mock.patch("requests.post") as post, \
                     mock.patch.object(app, "_record_exec_rejection"), \
                     mock.patch.object(app, "_record_diagnostic"):
                    result, code = app._send_broker_order(
                        "traderspost", "TradersPost", "MGC", self._payload(),
                        "https://broker.invalid", safety_context=context)
                self.assertEqual(code, 409)
                self.assertTrue(
                    ("freshness anchor" in result["reason"]
                     or "missing server analysis snapshot" in result["reason"]),
                    result["reason"],
                )
                post.assert_not_called()
                with app._BROKER_SIDE_LOCK:
                    self.assertEqual(app._BROKER_LAST_SIDE["MGC"], previous_side)

    def test_every_production_sink_call_passes_safety_context(self):
        """AST tripwire: a future sink call must explicitly name safety_context."""
        with open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_send_broker_order"
        ]
        self.assertGreaterEqual(len(calls), 6)
        for call in calls:
            self.assertTrue(
                any(keyword.arg == "safety_context" for keyword in call.keywords),
                "broker sink call bypasses the final safety boundary at line %s" % call.lineno,
            )


if __name__ == "__main__":
    unittest.main()