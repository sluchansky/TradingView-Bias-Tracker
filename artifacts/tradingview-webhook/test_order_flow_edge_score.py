"""Focused regression coverage for Order Flow V1's live Edge Score contribution."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

os.environ.setdefault("VISUAL_BRAIN_ENABLED", "0")
import app  # noqa: E402


def flow(score):
    return {"available": True, "order_flow_score": score}


class OrderFlowEdgeScoreTests(unittest.TestCase):
    def test_directional_adjustment_is_symmetric_and_bounded(self):
        self.assertEqual(app.order_flow_edge_adjustment(flow(100), "Long"), 15)
        self.assertEqual(app.order_flow_edge_adjustment(flow(100), "Short"), -15)
        self.assertEqual(app.order_flow_edge_adjustment(flow(0), "Long"), -15)
        self.assertEqual(app.order_flow_edge_adjustment(flow(0), "Short"), 15)
        self.assertEqual(app.order_flow_edge_adjustment(flow(50), "Long"), 0)

    def test_missing_or_malformed_flow_is_a_noop(self):
        self.assertEqual(app.order_flow_edge_adjustment(None, "Long"), 0)
        self.assertEqual(app.order_flow_edge_adjustment({"available": False}, "Long"), 0)
        self.assertEqual(app.order_flow_edge_adjustment(flow("bad"), "Long"), 0)
        self.assertEqual(app.order_flow_edge_adjustment(flow(float("nan")), "Long"), 0)

    def test_modifier_changes_the_shared_score_and_visible_breakdown(self):
        assessment = {
            # Canonical structure scoring is one active-cycle allocation, not
            # the retired raw BOS boolean component.
            "confluences": {"structure_allocation_points": 20},
            "session": {"preferred": False},
            "risk_label": "",
            "volatility": {},
        }
        entry = {
            "direction": "Long",
            "edge_modifiers": [{"label": "Order Flow Confirms Long", "points": 15}],
        }
        score, _ = app.compute_trade_edge_components(
            {"structure_allocation": 20},
            entry["edge_modifiers"],
        )
        breakdown = app.compute_edge_breakdown(assessment, entry)
        self.assertEqual(score, 35)
        self.assertEqual(breakdown["score"], 35)
        self.assertIn(
            {"label": "Order Flow Confirms Long", "points": 15},
            breakdown["score_breakdown"],
        )

    def test_order_flow_cannot_resurrect_a_hard_zero(self):
        assessment = {
            "confluences": {"bos": True, "choch": True, "vwap": True},
            "session": {"preferred": True},
            "zone_broken_active": True,
            "risk_label": "",
            "volatility": {},
        }
        entry = {
            "direction": "Long",
            "edge_modifiers": [{"label": "Order Flow Confirms Long", "points": 15}],
        }
        real_cfg = app.cfg
        with patch.object(
            app,
            "cfg",
            side_effect=lambda key: True if key == "GATE_REQUIRE_ZONE" else real_cfg(key),
        ):
            breakdown = app.compute_edge_breakdown(assessment, entry)
        self.assertEqual(breakdown["score"], 0)
        self.assertEqual(breakdown["score_breakdown"], [])


if __name__ == "__main__":
    unittest.main()