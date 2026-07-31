"""test_candidate_preview.py — Phase 7J.1

Tests for _mb_candidate_preview() and _mb_preview_from_plan().

Cases A–K: deterministic, in-process, no DB/network/Flask required.

Invariants verified:
  - No calculation is performed (values read verbatim from the plan dict)
  - No shared state is mutated
  - Fail-open: bad input yields NO_CANDIDATE or UNAVAILABLE, never raises
  - Priority: READY > POTENTIAL_Long > POTENTIAL_Short > NO_CANDIDATE
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import unittest

# ── Import helpers under test ─────────────────────────────────────────────────
# We import directly from the module; they are pure functions with no side-effects.
from app import _mb_candidate_preview, _mb_preview_from_plan


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_plan(direction="Long", trade_plan=True, **overrides):
    """Minimal build_strict_trade_plan-shaped dict."""
    base = {
        "trade_plan":               trade_plan,
        "direction":                direction,
        "entry_zone":               "52570.00–52578.00",
        "stop_loss":                "52555.00",
        "target1":                  "52593.00",
        "target2":                  "52593.00",
        "rr":                       "1:1",
        "risk_points":              18.0,
        "reward_points":            18.0,
        "risk_dollars_per_contract": 90.0,
        "atr_pts":                  7.25,
        "atr_multiplier":           1.5,
        "calculated_stop":          "52556.00",
        "stop_distance_ticks":      18,
        "stop_valid":               True,
        "stop_invalid_reason":      None,
        "instrument":               "MGC",
        "point_value":              10.0,
        "management": {
            "entry": 52574.0,
            "stop":  52555.0,
            "tp1":   52593.0,
        },
    }
    base.update(overrides)
    return base


def _make_result(strict_label="WAIT", trade_plan=None, directions=None, **extras):
    """Minimal full_analysis result dict."""
    r = {
        "strict_label":    strict_label,
        "strict_direction": None,
        "last_updated":    "2026-07-31T12:00:00",
    }
    if trade_plan is not None:
        r["trade_plan"] = trade_plan
    if directions is not None:
        r["directions"] = directions
    r.update(extras)
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMbPreviewFromPlan(unittest.TestCase):
    """Unit tests for _mb_preview_from_plan (the field-extractor helper)."""

    def test_extracts_all_canonical_fields(self):
        plan = _make_plan("Long")
        out  = _mb_preview_from_plan(plan, "READY", "Long")
        self.assertEqual(out["status"],       "READY")
        self.assertEqual(out["direction"],    "Long")
        self.assertEqual(out["readiness"],    "READY")
        self.assertEqual(out["entry_zone"],   "52570.00–52578.00")
        self.assertEqual(out["stop_loss"],    "52555.00")
        self.assertEqual(out["take_profit"],  "52593.00")
        self.assertEqual(out["risk_reward"],  "1:1")
        self.assertEqual(out["risk_points"],  18.0)
        self.assertAlmostEqual(out["risk_dollars_per_contract"], 90.0)
        self.assertAlmostEqual(out["atr"],    7.25)
        self.assertAlmostEqual(out["atr_multiplier"], 1.5)
        self.assertEqual(out["stop_ticks"],   18)
        self.assertTrue(out["stop_valid"])
        self.assertIsNone(out["stop_invalid_reason"])
        self.assertAlmostEqual(out["preview_price"], 52574.0)
        self.assertEqual(out["instrument"],   "MGC")

    def test_potential_sets_readiness_not_ready(self):
        plan = _make_plan("Short")
        out  = _mb_preview_from_plan(plan, "POTENTIAL", "Short")
        self.assertEqual(out["status"],    "POTENTIAL")
        self.assertEqual(out["readiness"], "NOT_READY")

    def test_no_management_block_preview_price_is_none(self):
        plan = _make_plan("Long")
        del plan["management"]
        out = _mb_preview_from_plan(plan, "READY", "Long")
        self.assertIsNone(out["preview_price"])

    def test_does_not_mutate_input_plan(self):
        plan = _make_plan("Long")
        original_plan = dict(plan)
        _mb_preview_from_plan(plan, "POTENTIAL", "Long")
        self.assertEqual(plan, original_plan)


class TestMbCandidatePreview(unittest.TestCase):

    # ── Case A: None result → NO_CANDIDATE ───────────────────────────────────
    def test_case_a_none_result(self):
        errors = []
        out = _mb_candidate_preview(None, errors)
        self.assertEqual(out["status"], "NO_CANDIDATE")
        self.assertIsNone(out["direction"])
        self.assertEqual(errors, [], "should not log an error for None result")

    # ── Case B: Empty result dict → NO_CANDIDATE ──────────────────────────────
    def test_case_b_empty_result(self):
        errors = []
        out = _mb_candidate_preview({}, errors)
        self.assertEqual(out["status"], "NO_CANDIDATE")

    # ── Case C: WAIT verdict, no directions key → NO_CANDIDATE ──────────────
    def test_case_c_wait_no_directions(self):
        r = _make_result(strict_label="WAIT")
        # no directions key at all
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"], "NO_CANDIDATE")

    # ── Case D: WAIT verdict + Long potential_plan present → POTENTIAL/Long ──
    def test_case_d_potential_long(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {"potential_plan": plan, "missing": ["volume_confirm"]}},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"],    "POTENTIAL")
        self.assertEqual(out["direction"], "Long")
        self.assertEqual(out["entry_zone"], "52570.00–52578.00")
        self.assertEqual(out["stop_loss"],  "52555.00")
        self.assertEqual(out["take_profit"], "52593.00")
        self.assertEqual(out["risk_reward"], "1:1")
        self.assertEqual(out["missing_confirmations"], ["volume_confirm"])
        self.assertAlmostEqual(out["atr"], 7.25)
        self.assertEqual(out["stop_ticks"], 18)
        self.assertAlmostEqual(out["preview_price"], 52574.0)
        self.assertEqual(errors, [])

    # ── Case E: WAIT verdict + Short potential_plan → POTENTIAL/Short ─────────
    def test_case_e_potential_short(self):
        plan = _make_plan("Short")
        r = _make_result(
            strict_label="WAIT",
            directions={"Short": {"potential_plan": plan}},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"],    "POTENTIAL")
        self.assertEqual(out["direction"], "Short")

    # ── Case F: potential_plan.trade_plan=False → NO_CANDIDATE ───────────────
    def test_case_f_potential_plan_trade_plan_false(self):
        plan = _make_plan("Long", trade_plan=False)
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {"potential_plan": plan}},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"], "NO_CANDIDATE")

    # ── Case G: READY verdict + trade_plan.trade_plan=True → READY ───────────
    def test_case_g_ready(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="READY",
            trade_plan=plan,
            strict_direction="Long",
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"],    "READY")
        self.assertEqual(out["direction"], "Long")
        self.assertEqual(out["readiness"], "READY")
        self.assertEqual(out["entry_zone"], "52570.00–52578.00")
        self.assertEqual(out["stop_loss"],  "52555.00")
        self.assertEqual(out["take_profit"], "52593.00")
        self.assertAlmostEqual(out["risk_dollars_per_contract"], 90.0)
        self.assertEqual(errors, [])

    # ── Case H: READY verdict + trade_plan.trade_plan=False → POTENTIAL fallthrough
    def test_case_h_ready_verdict_but_plan_false_falls_through_to_potential(self):
        tp     = _make_plan("Long", trade_plan=False)
        pp     = _make_plan("Long", trade_plan=True)
        r = _make_result(
            strict_label="READY",
            trade_plan=tp,
            directions={"Long": {"potential_plan": pp}},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        # READY path is skipped (trade_plan False), falls to POTENTIAL
        self.assertEqual(out["status"], "POTENTIAL")

    # ── Case I: POTENTIAL with missing list verified ──────────────────────────
    def test_case_i_missing_confirmations_forwarded(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {
                "potential_plan": plan,
                "missing": ["structure_confirm", "vwap_confirm"],
            }},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["missing_confirmations"],
                         ["structure_confirm", "vwap_confirm"])

    # ── Case J: Long + Short both have valid potential_plan → Long wins ───────
    def test_case_j_long_priority_over_short(self):
        long_plan  = _make_plan("Long")
        short_plan = _make_plan("Short")
        r = _make_result(
            strict_label="WAIT",
            directions={
                "Long":  {"potential_plan": long_plan},
                "Short": {"potential_plan": short_plan},
            },
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"],    "POTENTIAL")
        self.assertEqual(out["direction"], "Long")   # Long checked first

    # ── Case K: Non-list missing field is normalised to empty list ────────────
    def test_case_k_non_list_missing_normalised(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {"potential_plan": plan, "missing": None}},
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"], "POTENTIAL")
        self.assertEqual(out["missing_confirmations"], [])

    # ── Regression: READY label containing extra text still fires READY path ─
    def test_ready_label_substring_match(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="EARLY_READY",   # unlikely but defensive
            trade_plan=plan,
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["status"], "READY")

    # ── Regression: generated_at is forwarded from result ────────────────────
    def test_generated_at_forwarded(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {"potential_plan": plan}},
            last_updated="2026-07-31T09:30:00",
        )
        errors = []
        out = _mb_candidate_preview(r, errors)
        self.assertEqual(out["generated_at"], "2026-07-31T09:30:00")

    # ── Regression: does not mutate the result dict ───────────────────────────
    def test_does_not_mutate_result(self):
        plan = _make_plan("Long")
        r = _make_result(
            strict_label="WAIT",
            directions={"Long": {"potential_plan": plan}},
        )
        import copy
        original = copy.deepcopy(r)
        _mb_candidate_preview(r, [])
        self.assertEqual(r, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
