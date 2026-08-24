"""Focused display-only regressions for resolved structure-cycle explanations."""

import unittest

import app


def cycle(state, direction, confirmed, next_event, next_event_reason):
    return {
        "state": state,
        "direction": direction,
        "confirmed": confirmed,
        "next_event": next_event,
        "next_event_reason": next_event_reason,
        "summary": "Resolver-owned display summary.",
    }


class TestStructureExplanationConsistency(unittest.TestCase):
    def test_bearish_initial_cycle_keeps_same_direction_bos_requirement(self):
        raw = cycle(
            "TREND_INITIAL", "Short", False, "BOS SUPPLY",
            "Short BOS established initial directional structure only. "
            "Wait for BOS SUPPLY to confirm the continuation cycle.",
        )
        raw.update({"allocation_points": 20, "active_event": "BOS SUPPLY"})
        payload = app.build_main_brain_payload({
            "active_ticker": "MNQ",
            "strict_label": "SHORT WAIT",
            "structure_state": raw,
        })
        guidance = payload["verdict"]["structure_guidance"]
        self.assertEqual(guidance["next_event"], "BOS SUPPLY")
        self.assertIn("Wait for BOS SUPPLY", guidance["next_event_reason"])
        self.assertFalse(guidance["confirmed"])
        self.assertEqual(guidance["allocation_points"], 20)
        self.assertEqual(guidance["active_event"], "BOS SUPPLY")

        voice = app.compute_main_brain_voice({
            "active_ticker": "MNQ",
            "verdict": "SHORT WAIT",
            "main_brain": {"what_now": ["a generic CHOCH"]},
            "structure_state": raw,
        })
        self.assertIn("Wait for BOS SUPPLY", voice["narration"])
        self.assertNotIn("generic CHOCH", voice["narration"])

    def test_bullish_initial_cycle_keeps_same_direction_bos_requirement(self):
        raw = cycle(
            "TREND_INITIAL", "Long", False, "BOS DEMAND",
            "Long BOS established initial directional structure only. "
            "Wait for BOS DEMAND to confirm the continuation cycle.",
        )
        guidance = app._structure_cycle_operator_guidance(raw)
        self.assertEqual(guidance["next_event"], "BOS DEMAND")
        self.assertIn("continuation", guidance["next_event_reason"])

    def test_choch_is_only_described_as_a_reversal_candidate(self):
        raw = cycle(
            "REVERSAL_CANDIDATE", "Short", False, "BOS SUPPLY",
            "Short CHOCH is a reversal candidate only. "
            "Wait for BOS SUPPLY to confirm the new structure cycle.",
        )
        voice = app.compute_main_brain_voice({
            "active_ticker": "MNQ",
            "verdict": "SHORT WAIT",
            "structure_state": raw,
        })
        self.assertIn("CHOCH is a reversal candidate only", voice["narration"])
        self.assertIn("BOS SUPPLY", voice["narration"])

    def test_confirmed_reversal_does_not_create_a_wait_requirement(self):
        raw = cycle(
            "REVERSAL_CONFIRMED", "Long", True, "CHOCH SUPPLY",
            "Current long structure is confirmed. The next valid state change is "
            "CHOCH SUPPLY, a new reversal candidate.",
        )
        raw.update({"allocation_points": 40, "last_event": "BOS DEMAND"})
        payload = app.build_main_brain_payload({
            "active_ticker": "MNQ",
            "strict_label": "LONG WAIT",
            "structure_state": raw,
        })
        guidance = payload["verdict"]["structure_guidance"]
        self.assertEqual(guidance["allocation_points"], 40)
        self.assertEqual(guidance["last_event"], "BOS DEMAND")
        voice = app.compute_main_brain_voice({
            "active_ticker": "MNQ",
            "verdict": "LONG WAIT",
            "structure_state": raw,
        })
        self.assertNotIn("CHOCH SUPPLY", voice["narration"])


if __name__ == "__main__":
    unittest.main()