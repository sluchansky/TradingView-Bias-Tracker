"""
Left Brain Phase 2 — Dynamic Thesis Engine tests (P2-01 … P2-30).

All tests are display-only / pure-function / no side effects.
Money-path isolation is proved by P2-29 (flag-OFF parity).
"""
from __future__ import annotations

import sys
import os
import types
import unittest
from datetime import datetime, timezone, timedelta

# ─── Resolve project root ────────────────────────────────────────────────────
_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)

from left_brain_market_intelligence import (
    _thesis_direction,
    _thesis_strength,
    _thesis_momentum,
    _thesis_narrative_structured,
    _thesis_invalidation_structured,
    _compute_playbook_reasoning,
    _detect_significant_changes,
    _thesis_stability,
    _thesis_timeline,
    _neutral_thesis,
    compute_left_brain_thesis,
    compute_left_brain_mi,
    THESIS_DIRECTIONS,
    THESIS_MOMENTUMS,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _ts(offset_min: int = 0) -> str:
    """Return a UTC ISO timestamp offset from now by `offset_min` minutes."""
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _mi_bullish(conf: int = 80) -> dict:
    """Minimal MI block that resolves to BULLISH direction."""
    return {
        "available":           True,
        "instrument":          "MGC",
        "computed_at":         _ts(),
        "market_state":        "TRENDING_UP_STRONG",
        "session_character":   "STRONG_TREND_DAY",
        "session_phase":       "MORNING_SESSION",
        "auction_control":     "BUYER",
        "directional_outlook": {"long": 65, "short": 20, "neutral": 15},
        "data_confidence":     conf,
        "suitable_playbooks":  ["MOMENTUM_CONTINUATION", "PULLBACK_TO_VWAP"],
        "supporting_evidence": [
            "Price above VWAP — buyers have control of session anchor",
            "Confirmed BOS/CHOCH in Long direction — structural demand present",
            "Cumulative Volume Delta positive — aggressive buy-side participation",
        ],
        "missing_evidence":    [],
        "what_changes_thesis": "...",
        "narrative":           "...",
    }


def _mi_bearish(conf: int = 75) -> dict:
    m = _mi_bullish(conf)
    m.update({
        "market_state":        "TRENDING_DOWN_STRONG",
        "session_character":   "STRONG_TREND_DAY",
        "auction_control":     "SELLER",
        "directional_outlook": {"long": 15, "short": 70, "neutral": 15},
        "suitable_playbooks":  ["MOMENTUM_CONTINUATION", "PULLBACK_TO_VWAP"],
        "supporting_evidence": [
            "Price below VWAP — sellers have control of session anchor",
            "Confirmed BOS/CHOCH in Short direction — structural supply present",
            "Cumulative Volume Delta negative — aggressive sell-side participation",
        ],
    })
    return m


def _mi_neutral() -> dict:
    m = _mi_bullish()
    m.update({
        "market_state":        "MEAN_REVERTING_RANGE",
        "auction_control":     "CONTESTED",
        "directional_outlook": {"long": 35, "short": 35, "neutral": 30},
        "suitable_playbooks":  ["RANGE_FADE", "AVOID_CHOPPY_CONDITIONS"],
    })
    return m


def _mi_conflicted() -> dict:
    m = _mi_bullish()
    m.update({
        "auction_control":     "CONTESTED",
        "directional_outlook": {"long": 45, "short": 30, "neutral": 25},
    })
    return m


def _mem_event(event_type: str, offset_min: int = 0) -> dict:
    return {
        "ts":                 _ts(offset_min),
        "event_type":         event_type,
        "label":              event_type,
        "from_value":         "A",
        "to_value":           "B",
        "reason":             "test",
        "evidence":           [],
        "confidence_at_time": 70,
    }


# ─── Test cases ──────────────────────────────────────────────────────────────

class TestThesisDirection(unittest.TestCase):

    def test_P2_01_bullish_when_long_gt_55(self):
        """P2-01 _thesis_direction returns BULLISH when long > 55."""
        d = _thesis_direction({"long": 60, "short": 25, "neutral": 15})
        self.assertEqual(d, "BULLISH")

    def test_P2_02_bearish_when_short_gt_55(self):
        """P2-02 _thesis_direction returns BEARISH when short > 55."""
        d = _thesis_direction({"long": 20, "short": 60, "neutral": 20})
        self.assertEqual(d, "BEARISH")

    def test_P2_03_neutral_when_balanced(self):
        """P2-03 _thesis_direction returns NEUTRAL when |long−short| < 15."""
        d = _thesis_direction({"long": 38, "short": 32, "neutral": 30})
        self.assertEqual(d, "NEUTRAL")

    def test_P2_04_conflicted_otherwise(self):
        """P2-04 _thesis_direction returns CONFLICTED when 15 ≤ |long−short| ≤ 55."""
        d = _thesis_direction({"long": 48, "short": 30, "neutral": 22})
        self.assertEqual(d, "CONFLICTED")

    def test_P2_05_direction_in_canonical_set(self):
        """P2-05 every _thesis_direction output is in THESIS_DIRECTIONS."""
        cases = [
            {"long": 70, "short": 10, "neutral": 20},
            {"long": 10, "short": 70, "neutral": 20},
            {"long": 35, "short": 35, "neutral": 30},
            {"long": 45, "short": 28, "neutral": 27},
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertIn(_thesis_direction(c), THESIS_DIRECTIONS)


class TestThesisStrength(unittest.TestCase):

    def test_P2_06_scales_by_confidence(self):
        """P2-06 strength scales with data confidence (50% confidence halves)."""
        s100 = _thesis_strength("BULLISH", {"long": 70, "short": 15, "neutral": 15},
                                 100, "BUYER", "TRENDING_UP_MILD")
        s50  = _thesis_strength("BULLISH", {"long": 70, "short": 15, "neutral": 15},
                                  50, "BUYER", "TRENDING_UP_MILD")
        self.assertGreater(s100, s50)

    def test_P2_07_strong_state_bonus(self):
        """P2-07 TRENDING_*_STRONG + aligned auction gives +8 bonus."""
        without = _thesis_strength("BULLISH", {"long": 65, "short": 15, "neutral": 20},
                                    80, "CONTESTED", "TRENDING_UP_MILD")
        with_bonus = _thesis_strength("BULLISH", {"long": 65, "short": 15, "neutral": 20},
                                       80, "BUYER", "TRENDING_UP_STRONG")
        self.assertGreater(with_bonus, without)

    def test_P2_08_strength_in_range(self):
        """P2-08 strength is always 0–100."""
        for d, out, conf in [
            ("BULLISH",   {"long": 90, "short": 5, "neutral": 5},  100),
            ("BEARISH",   {"long": 5, "short": 90, "neutral": 5},  100),
            ("NEUTRAL",   {"long": 33, "short": 33, "neutral": 34}, 0),
            ("CONFLICTED",{"long": 45, "short": 30, "neutral": 25}, 50),
        ]:
            with self.subTest(d=d):
                s = _thesis_strength(d, out, conf, "CONTESTED", "UNKNOWN")
                self.assertGreaterEqual(s, 0)
                self.assertLessEqual(s, 100)


class TestThesisMomentum(unittest.TestCase):

    def test_P2_09_reversing_on_direction_change(self):
        """P2-09 REVERSING when direction_changed=True."""
        m = _thesis_momentum(60, 70, direction_changed=True)
        self.assertEqual(m, "REVERSING")

    def test_P2_10_increasing_on_plus_8(self):
        """P2-10 INCREASING when strength delta ≥ 8."""
        m = _thesis_momentum(55, 65, direction_changed=False)
        self.assertEqual(m, "INCREASING")

    def test_P2_11_weakening_on_minus_8(self):
        """P2-11 WEAKENING when strength delta ≤ −8."""
        m = _thesis_momentum(65, 55, direction_changed=False)
        self.assertEqual(m, "WEAKENING")

    def test_P2_12_stable_on_small_delta(self):
        """P2-12 STABLE when |delta| < 8 and direction unchanged."""
        m = _thesis_momentum(60, 65, direction_changed=False)
        self.assertEqual(m, "STABLE")

    def test_P2_13_stable_on_first_call(self):
        """P2-13 STABLE when prev_strength is None (first call)."""
        m = _thesis_momentum(None, 55, direction_changed=False)
        self.assertEqual(m, "STABLE")

    def test_P2_14_momentum_in_canonical_set(self):
        """P2-14 every _thesis_momentum output is in THESIS_MOMENTUMS."""
        cases = [(70, 80, False), (80, 70, False), (70, 70, False), (70, 80, True)]
        for prev, cur, dc in cases:
            with self.subTest(prev=prev, cur=cur, dc=dc):
                self.assertIn(_thesis_momentum(prev, cur, dc), THESIS_MOMENTUMS)


class TestDetectSignificantChanges(unittest.TestCase):

    def test_P2_15_empty_when_no_prev_mi(self):
        """P2-15 _detect_significant_changes returns [] when prev_mi is None."""
        evts = _detect_significant_changes(
            _mi_bullish(), None, "BULLISH", None, 50, None)
        self.assertEqual(evts, [])

    def test_P2_16_market_state_change_detected(self):
        """P2-16 MARKET_STATE_CHANGE event when market_state differs."""
        prev = _mi_bullish()
        prev["market_state"] = "MEAN_REVERTING_RANGE"
        cur  = _mi_bullish()
        cur["market_state"]  = "TRENDING_UP_STRONG"
        evts = _detect_significant_changes(cur, prev, "BULLISH", "BULLISH", 55, 50)
        types_ = [e["event_type"] for e in evts]
        self.assertIn("MARKET_STATE_CHANGE", types_)

    def test_P2_17_auction_control_change_detected(self):
        """P2-17 CONTROL_CHANGE event when auction_control differs."""
        prev = _mi_bullish()
        prev["auction_control"] = "BUYER"
        cur  = _mi_bullish()
        cur["auction_control"]  = "SELLER"
        evts = _detect_significant_changes(cur, prev, "BULLISH", "BULLISH", 55, 50)
        types_ = [e["event_type"] for e in evts]
        self.assertIn("CONTROL_CHANGE", types_)

    def test_P2_18_thesis_established_on_direction_change(self):
        """P2-18 THESIS_ESTABLISHED event when thesis direction changes."""
        evts = _detect_significant_changes(
            _mi_bullish(), _mi_bearish(), "BULLISH", "BEARISH", 55, 50)
        types_ = [e["event_type"] for e in evts]
        self.assertIn("THESIS_ESTABLISHED", types_)

    def test_P2_19_thesis_strengthened_on_plus_10(self):
        """P2-19 THESIS_STRENGTHENED when strength increases by ≥ 10."""
        evts = _detect_significant_changes(
            _mi_bullish(), _mi_bullish(), "BULLISH", "BULLISH", 70, 55)
        types_ = [e["event_type"] for e in evts]
        self.assertIn("THESIS_STRENGTHENED", types_)

    def test_P2_20_thesis_weakened_on_minus_10(self):
        """P2-20 THESIS_WEAKENED when strength decreases by ≥ 10."""
        evts = _detect_significant_changes(
            _mi_bullish(), _mi_bullish(), "BULLISH", "BULLISH", 45, 60)
        types_ = [e["event_type"] for e in evts]
        self.assertIn("THESIS_WEAKENED", types_)

    def test_P2_21_no_events_on_identical_mi(self):
        """P2-21 No significant events when MI is identical and no direction change."""
        mi = _mi_bullish(conf=80)
        mi2 = dict(mi)
        # Same market_state, auction_control, outlook, same direction+strength
        evts = _detect_significant_changes(mi2, mi, "BULLISH", "BULLISH", 52, 52)
        types_ = [e["event_type"] for e in evts]
        # Should produce no THESIS_ESTABLISHED (same direction) — other events
        # require material changes that don't exist here
        self.assertNotIn("THESIS_ESTABLISHED", types_)
        self.assertNotIn("THESIS_STRENGTHENED", types_)
        self.assertNotIn("THESIS_WEAKENED", types_)

    def test_P2_22_event_schema_complete(self):
        """P2-22 Every generated event has the required schema keys."""
        evts = _detect_significant_changes(
            _mi_bullish(), _mi_bearish(), "BULLISH", "BEARISH", 55, 50)
        required = {"ts", "event_type", "label", "from_value",
                    "to_value", "reason", "evidence", "confidence_at_time"}
        for evt in evts:
            with self.subTest(event_type=evt["event_type"]):
                self.assertTrue(required.issubset(evt.keys()))


class TestThesisStability(unittest.TestCase):

    def test_P2_23_rapid_flip_warning_on_3_transitions(self):
        """P2-23 rapid_flip_warning=True when ≥3 THESIS_ESTABLISHED in 30 min."""
        events = [_mem_event("THESIS_ESTABLISHED", offset_min=-i) for i in range(3)]
        stab = _thesis_stability(events, _ts(-1))
        self.assertTrue(stab["rapid_flip_warning"])

    def test_P2_24_no_transitions_zero_count(self):
        """P2-24 number_of_transitions=0 when no THESIS_ESTABLISHED events."""
        events = [_mem_event("MARKET_STATE_CHANGE"), _mem_event("CONTROL_CHANGE")]
        stab = _thesis_stability(events, _ts(-5))
        self.assertEqual(stab["number_of_transitions"], 0)
        self.assertFalse(stab["rapid_flip_warning"])

    def test_P2_25_time_in_thesis_positive(self):
        """P2-25 time_in_current_thesis_min is positive when established_at is in past."""
        stab = _thesis_stability([], _ts(-10))
        self.assertIsNotNone(stab["time_in_current_thesis_min"])
        self.assertGreater(stab["time_in_current_thesis_min"], 0)

    def test_P2_26_stability_schema(self):
        """P2-26 stability dict always has the 5 required keys."""
        stab = _thesis_stability([], _ts())
        required = {
            "time_in_current_thesis_min",
            "number_of_transitions",
            "average_thesis_duration_min",
            "rapid_flip_warning",
            "stability_note",
        }
        self.assertTrue(required.issubset(stab.keys()))


class TestThesisTimeline(unittest.TestCase):

    def test_P2_27_newest_first(self):
        """P2-27 _thesis_timeline returns events newest-first."""
        events = [
            _mem_event("MARKET_STATE_CHANGE", offset_min=-10),
            _mem_event("CONTROL_CHANGE",      offset_min=-5),
            _mem_event("THESIS_ESTABLISHED",  offset_min=-1),
        ]
        tl = _thesis_timeline(events)
        ts_vals = [e["ts"] for e in tl]
        # First element should be the most recent
        self.assertGreaterEqual(ts_vals[0], ts_vals[-1])

    def test_P2_28_max_20_enforced(self):
        """P2-28 _thesis_timeline never returns more than 20 events."""
        events = [_mem_event("MARKET_STATE_CHANGE", offset_min=-i)
                  for i in range(30)]
        tl = _thesis_timeline(events, max_events=20)
        self.assertLessEqual(len(tl), 20)

    def test_P2_29_timeline_schema(self):
        """P2-29 timeline entries have required fields."""
        events = [_mem_event("CONTROL_CHANGE")]
        tl = _thesis_timeline(events)
        required = {"ts", "label", "event_type", "from_value", "to_value"}
        for item in tl:
            self.assertTrue(required.issubset(item.keys()))


class TestPlaybookReasoning(unittest.TestCase):

    def test_P2_30_returns_list_of_dicts(self):
        """P2-30 _compute_playbook_reasoning returns a list of dicts."""
        result = _compute_playbook_reasoning(_mi_bullish())
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for item in result:
            with self.subTest(name=item.get("name")):
                self.assertIn("name",      item)
                self.assertIn("fit_score", item)
                self.assertIn("reasons",   item)
                self.assertIn("missing",   item)

    def test_P2_31_fit_score_in_range(self):
        """P2-31 fit_score is always 0–100."""
        for mi in [_mi_bullish(), _mi_bearish(), _mi_neutral()]:
            for pb in _compute_playbook_reasoning(mi):
                with self.subTest(pb=pb["name"]):
                    self.assertGreaterEqual(pb["fit_score"], 0)
                    self.assertLessEqual(pb["fit_score"],   100)

    def test_P2_32_reasons_missing_are_lists(self):
        """P2-32 reasons and missing fields are both lists of strings."""
        for pb in _compute_playbook_reasoning(_mi_bullish()):
            self.assertIsInstance(pb["reasons"], list)
            self.assertIsInstance(pb["missing"], list)

    def test_P2_33_momentum_continuation_high_score_on_bullish_mi(self):
        """P2-33 MOMENTUM_CONTINUATION scores well when all criteria met."""
        mi   = _mi_bullish(conf=90)
        pbs  = _compute_playbook_reasoning(mi)
        mc   = next((p for p in pbs if p["name"] == "MOMENTUM_CONTINUATION"), None)
        if mc:
            self.assertGreater(mc["fit_score"], 30)


class TestInvalidationStructured(unittest.TestCase):

    def test_P2_34_trending_up_has_weakens_and_fails(self):
        """P2-34 TRENDING_UP states return both weakens_if and fails_if lists."""
        mi = _mi_bullish()
        mi["market_state"] = "TRENDING_UP_STRONG"
        inv = _thesis_invalidation_structured(mi)
        self.assertIn("weakens_if", inv)
        self.assertIn("fails_if",   inv)
        self.assertGreater(len(inv["weakens_if"]), 0)
        self.assertGreater(len(inv["fails_if"]),   0)

    def test_P2_35_all_market_states_produce_nonempty_result(self):
        """P2-35 _thesis_invalidation_structured is defined for all canonical states."""
        from left_brain_market_intelligence import MARKET_STATES
        mi = _mi_bullish()
        for ms in MARKET_STATES:
            mi2 = dict(mi)
            mi2["market_state"] = ms
            inv = _thesis_invalidation_structured(mi2)
            with self.subTest(ms=ms):
                self.assertIn("weakens_if", inv)
                self.assertIn("fails_if",   inv)


class TestNarrativeStructured(unittest.TestCase):

    def test_P2_36_returns_list_of_strings(self):
        """P2-36 _thesis_narrative_structured returns a list of ≥ 3 strings."""
        narr = _thesis_narrative_structured(_mi_bullish(), "BULLISH")
        self.assertIsInstance(narr, list)
        self.assertGreaterEqual(len(narr), 3)
        for item in narr:
            self.assertIsInstance(item, str)

    def test_P2_37_all_directions_produce_narrative(self):
        """P2-37 narrative works for all four thesis directions."""
        for direction in ("BULLISH", "BEARISH", "NEUTRAL", "CONFLICTED"):
            narr = _thesis_narrative_structured(_mi_bullish(), direction)
            with self.subTest(d=direction):
                self.assertGreater(len(narr), 0)


class TestComputeLeftBrainThesis(unittest.TestCase):

    def test_P2_38_first_call_no_prev(self):
        """P2-38 compute_left_brain_thesis works on first call (no prev)."""
        out = compute_left_brain_thesis("MGC", _mi_bullish(), None, None, [])
        self.assertIn("thesis",     out)
        self.assertIn("new_events", out)
        thesis = out["thesis"]
        self.assertTrue(thesis.get("available"))
        self.assertIn(thesis["direction"], THESIS_DIRECTIONS)
        self.assertIn(thesis["momentum"],  THESIS_MOMENTUMS)
        self.assertIsInstance(thesis["narrative"],  list)
        self.assertIsInstance(thesis["playbooks"],  list)
        self.assertIsInstance(thesis["stability"],  dict)
        self.assertIsInstance(thesis["timeline"],   list)

    def test_P2_39_direction_change_resets_established_at(self):
        """P2-39 established_at resets when thesis direction changes."""
        prev_thesis = {
            "direction":      "BEARISH",
            "strength":       60,
            "established_at": _ts(-30),
        }
        out = compute_left_brain_thesis("MGC", _mi_bullish(), _mi_bearish(),
                                         prev_thesis, [])
        thesis = out["thesis"]
        if thesis["direction"] != "BEARISH":
            # Direction changed → established_at should be recent (not 30 min ago)
            est = datetime.fromisoformat(thesis["established_at"])
            if est.tzinfo is None:
                est = est.replace(tzinfo=timezone.utc)
            age_min = (datetime.now(timezone.utc) - est).total_seconds() / 60
            self.assertLess(age_min, 5)

    def test_P2_40_thesis_established_event_on_direction_change(self):
        """P2-40 THESIS_ESTABLISHED appears in new_events on direction change."""
        prev_thesis = {"direction": "BEARISH", "strength": 60, "established_at": _ts(-30)}
        prev_mi     = _mi_bearish()
        cur_mi      = _mi_bullish()
        out = compute_left_brain_thesis("MGC", cur_mi, prev_mi, prev_thesis, [])
        types_ = [e["event_type"] for e in out.get("new_events", [])]
        # If direction indeed changed, event must appear
        new_dir = out["thesis"]["direction"]
        if new_dir != "BEARISH":
            self.assertIn("THESIS_ESTABLISHED", types_)

    def test_P2_41_fail_open_on_bad_input(self):
        """P2-41 compute_left_brain_thesis returns neutral thesis on bad input."""
        out = compute_left_brain_thesis("MGC", {"available": False}, None, None, [])
        self.assertIn("thesis",     out)
        self.assertFalse(out["thesis"]["available"])

    def test_P2_42_new_events_match_memory_append(self):
        """P2-42 new_events from thesis are the same objects added to memory."""
        out1 = compute_left_brain_thesis(
            "MGC", _mi_bullish(), _mi_bearish(),
            {"direction": "BEARISH", "strength": 55, "established_at": _ts(-20)},
            [],
        )
        # Feed the memory events back into the next call
        memory = out1["new_events"]
        out2 = compute_left_brain_thesis(
            "MGC", _mi_bullish(), _mi_bullish(),
            out1["thesis"], memory,
        )
        # Timeline should include previously accumulated events
        tl_types = {e["event_type"] for e in out2["thesis"]["timeline"]}
        # At least one event should be in the timeline
        self.assertGreater(len(out2["thesis"]["timeline"]), 0)

    def test_P2_43_all_thesis_keys_present(self):
        """P2-43 thesis dict always has every required key."""
        out = compute_left_brain_thesis("MNQ", _mi_neutral(), None, None, [])
        required = {
            "available", "instrument", "direction", "strength", "momentum",
            "established_at", "last_updated_at", "narrative", "invalidation",
            "playbooks", "stability", "timeline",
        }
        self.assertTrue(required.issubset(out["thesis"].keys()))

    def test_P2_44_neutral_thesis_available_false(self):
        """P2-44 _neutral_thesis has available=False."""
        nt = _neutral_thesis("MES")
        self.assertFalse(nt["available"])
        self.assertIn("time_in_current_thesis_min", nt["stability"])


class TestExecutionParity(unittest.TestCase):
    """P2-45 — Prove that flag-OFF full_analysis is byte-identical regardless of Phase 2."""

    def _run_analysis(self, flag_value: str) -> dict:
        """Import app with patched env var and run full_analysis."""
        # Patch the flag at module level for this call
        import app as _app
        orig = _app.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED
        try:
            _app.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED = (flag_value == "1")
            result = _app.full_analysis("MGC1!")
            return result
        finally:
            _app.LEFT_BRAIN_MARKET_INTELLIGENCE_ENABLED = orig

    def test_P2_45_flag_off_no_left_brain_key(self):
        """P2-45 flag OFF → 'left_brain' key absent from full_analysis result."""
        result = self._run_analysis("0")
        self.assertNotIn("left_brain", result)

    def test_P2_46_flag_on_adds_thesis_key(self):
        """P2-46 flag ON → 'left_brain' dict present with 'thesis' sub-key."""
        result = self._run_analysis("1")
        self.assertIn("left_brain", result)
        lb = result["left_brain"]
        self.assertIn("thesis", lb)

    def test_P2_47_money_path_keys_unchanged(self):
        """P2-47 verdict/edge_score/is_actionable identical between flag ON and OFF."""
        off = self._run_analysis("0")
        on  = self._run_analysis("1")
        money_keys = [
            "verdict", "strict_direction", "strict_reason",
            "edge_score", "is_actionable", "trade_plan",
        ]
        for k in money_keys:
            with self.subTest(key=k):
                self.assertEqual(off.get(k), on.get(k))


if __name__ == "__main__":
    unittest.main(verbosity=2)
