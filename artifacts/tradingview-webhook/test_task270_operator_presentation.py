"""Focused regression coverage for Task #270's read-only operator contract.

Run:
  python3 artifacts/tradingview-webhook/test_task270_operator_presentation.py

No test here invokes a broker, gate, database write, persistence path, or the
Flask server. The assertions exercise only display projection and response-copy
serialization helpers.
"""
import math
import os
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
import app  # noqa: E402


def _result(inst, mode, label, reason, price, vwap, vwap_status="ok"):
    return {
        "active_ticker": inst,
        "trading_mode": mode,
        "strict_label": label,
        "strict_reason": reason,
        "strict_direction": None,
        "strict_missing": ["structure_confirmed"],
        "current_price": price,
        "vwap_value": vwap,
        "vwap_status": vwap_status,
        "structure_state": {
            "state": "TREND_INITIAL",
            "direction": "Short",
            "confirmed": False,
            "next_event": "BOS SUPPLY",
            "next_event_reason": "Wait for BOS SUPPLY confirmation.",
            "summary": "Short structure is still initial.",
        },
    }


class TestOperatorPresentationMatrix(unittest.TestCase):
    def test_all_instruments_and_modes_keep_wait_candidate_non_actionable(self):
        for inst in ("MGC", "MNQ", "MES", "MYM"):
            for mode in ("SCALP", "SWING", "INTRADAY_TREND"):
                with self.subTest(inst=inst, mode=mode):
                    presentation = app._build_operator_presentation(_result(
                        inst, mode, "WAIT", "Short WAIT — structure pending.",
                        100.0, 101.0,
                    ))
                    self.assertEqual(presentation["verdict"], "WAIT")
                    self.assertEqual(presentation["candidate_direction"], "Short")
                    self.assertIsNone(presentation["actionable_direction"])
                    self.assertFalse(presentation["is_actionable"])
                    self.assertEqual(presentation["vwap"]["side"], "BELOW")
                    self.assertIn("below VWAP", presentation["vwap"]["wording"])
                    self.assertEqual(
                        presentation["structure_guidance"]["next_event_reason"],
                        "Wait for BOS SUPPLY confirmation.",
                    )

    def test_ready_direction_is_actionable_but_wait_direction_is_only_candidate(self):
        ready = app._build_operator_presentation(_result(
            "MGC", "SCALP", "LONG READY", "Long READY — all gates passed.",
            102.0, 101.0,
        ))
        waiting = app._build_operator_presentation(_result(
            "MGC", "SCALP", "WAIT", "Long WAIT — volume pending.",
            102.0, 101.0,
        ))
        self.assertEqual(ready["candidate_direction"], "Long")
        self.assertEqual(ready["actionable_direction"], "Long")
        self.assertTrue(ready["is_actionable"])
        self.assertEqual(waiting["candidate_direction"], "Long")
        self.assertIsNone(waiting["actionable_direction"])
        self.assertFalse(waiting["is_actionable"])

    def test_vwap_above_below_and_unavailable_are_authoritative(self):
        above = app._build_operator_presentation(_result(
            "MNQ", "SWING", "WAIT", "Long WAIT — pending.", 102.0, 101.0,
        ))
        below = app._build_operator_presentation(_result(
            "MNQ", "SWING", "WAIT", "Short WAIT — pending.", 100.0, 101.0,
        ))
        unavailable = app._build_operator_presentation(_result(
            "MNQ", "SWING", "WAIT", "WAIT — VWAP pending.", 100.0, None, "stale",
        ))
        self.assertEqual(above["vwap"]["side"], "ABOVE")
        self.assertEqual(below["vwap"]["side"], "BELOW")
        self.assertEqual(unavailable["vwap"]["side"], "UNAVAILABLE")
        self.assertIn("unavailable", unavailable["vwap"]["wording"].lower())

    def test_voice_uses_strict_candidate_not_independent_favored_direction(self):
        result = _result(
            "MNQ", "SCALP", "WAIT",
            "Short WAIT — failed gate(s): structure_confirmed.",
            100.0, 101.0,
        )
        result["main_brain"] = {"favored_direction": "Long"}
        result["operator_presentation"] = app._build_operator_presentation(result)
        voice = app.compute_main_brain_voice(result)
        self.assertIn("Short candidate", voice["headline"])
        self.assertIn("Short WAIT", voice["narration"])
        self.assertNotIn("leaning long", voice["narration"].lower())


class TestStatusResponseBoundary(unittest.TestCase):
    def test_nested_non_json_values_are_copied_and_normalized(self):
        cycle = {}
        cycle["self"] = cycle
        source = {
            "when": datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
            "decimal": Decimal("12.5"),
            "tuple": (1, Decimal("2.5")),
            "set": {"b", "a"},
            "bytes": b"ok",
            "nan": math.nan,
            "nested": {"cycle": cycle, "unexpected": object()},
        }
        safe = app._status_json_safe_payload(source)
        self.assertIsNot(safe, source)
        self.assertEqual(safe["when"], "2026-08-24T12:30:00+00:00")
        self.assertEqual(safe["decimal"], 12.5)
        self.assertEqual(safe["tuple"], [1, 2.5])
        self.assertEqual(safe["set"], ["a", "b"])
        self.assertEqual(safe["bytes"], "ok")
        self.assertIsNone(safe["nan"])
        self.assertIsNone(safe["nested"]["cycle"]["self"])
        self.assertIsInstance(safe["nested"]["unexpected"], str)
        self.assertIsInstance(source["tuple"], tuple)
        self.assertTrue(math.isnan(source["nan"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)