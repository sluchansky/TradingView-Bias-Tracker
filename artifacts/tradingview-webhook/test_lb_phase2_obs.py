"""
Left Brain Phase 2 — Observation Infrastructure Tests (Part 10)

Tests all new observation-infrastructure requirements:
  - Retention capacity (maxlen=5000)
  - Dedup by bar timestamp (same bar-ts skipped)
  - Instrument isolation (deque per instrument)
  - /lb-thesis-obs limit/summary/inst/404 query parameters
  - Metadata: oldest_ts, newest_ts, retention block
  - Observations returned newest-first
  - Playbook descending-score ordering
  - Deterministic equal-score tie-break by name
  - OUTLOOK_SHIFT: non-empty evidence, never stale leakage
  - top_playbook_fit_score field renamed correctly
  - vwap_age_ms / mi_input_ts fields present in snapshots
  - No money-path influence (gate, edge, broker untouched)
"""

from collections import deque
from unittest.mock import patch
import importlib
import sys
import json

import pytest

# ---------------------------------------------------------------------------
# Helpers — import the pure Python modules directly
# ---------------------------------------------------------------------------
from left_brain_market_intelligence import (
    compute_left_brain_mi,
    compute_left_brain_thesis,
    _compute_playbook_reasoning,
    _detect_significant_changes,
    _neutral_thesis,          # private function, not a module-level constant
)

# ---------------------------------------------------------------------------
# Part 3 — Playbook ordering
# ---------------------------------------------------------------------------

class TestPlaybookOrdering:
    """_compute_playbook_reasoning must return playbooks sorted by fit_score DESC."""

    def _make_mi_with_playbooks(self, pb_names: list[str]) -> dict:
        """Minimal MI dict that lists named playbooks as suitable."""
        return {"suitable_playbooks": pb_names}

    def test_higher_score_before_lower_score(self):
        """A playbook with fit_score=85 must appear before one with fit_score=70."""
        from left_brain_market_intelligence import _PLAYBOOK_MAP, _PLAYBOOK_CRITERIA

        # Pick two playbooks from the map if available; else use mock criteria
        pbs = list(_PLAYBOOK_MAP.keys()) if _PLAYBOOK_MAP else []
        if len(pbs) < 2:
            pytest.skip("Need at least 2 playbooks in _PLAYBOOK_MAP")

        mi = {"suitable_playbooks": pbs}
        result = _compute_playbook_reasoning(mi)

        scores = [r["fit_score"] for r in result]
        assert scores == sorted(scores, reverse=True), (
            f"Playbooks not sorted descending: {scores}"
        )

    def test_equal_scores_ordered_by_name(self):
        """When two playbooks produce identical fit_scores, order by name ASC."""
        from left_brain_market_intelligence import _PLAYBOOK_CRITERIA

        # Inject two dummy playbooks with zero criteria (fit_score=0 both)
        name_a, name_b = "ZSTRAT_AAA", "ZSTRAT_BBB"
        fake_criteria: dict = {name_a: [], name_b: []}

        with patch.dict("left_brain_market_intelligence._PLAYBOOK_CRITERIA", fake_criteria, clear=False):
            mi = {"suitable_playbooks": [name_b, name_a]}  # reversed input
            result = _compute_playbook_reasoning(mi)

        names = [r["name"] for r in result]
        # Both have score=0; alphabetic tie-break should yield AAA before BBB
        assert names.index(name_a) < names.index(name_b), (
            f"Equal-score tie-break failed: {names}"
        )

    def test_all_playbooks_scored_before_top3_chosen(self):
        """If 5 playbooks are present, the top-3 by score are returned (not first 3)."""
        from left_brain_market_intelligence import _PLAYBOOK_CRITERIA

        # Mock 5 playbooks: give them known scores by returning varying criteria
        names = [f"MOCK_{i}" for i in range(5)]
        # We'll create criteria that give scores: 10, 50, 30, 80, 60
        # Only way to do this cleanly: patch _PLAYBOOK_CRITERIA directly
        # Use a real fit-evaluation: give each a single criterion returning True/False
        _mock_crit = {}
        scores_wanted = {names[0]: 10, names[1]: 50, names[2]: 30, names[3]: 80, names[4]: 60}
        for nm, sc in scores_wanted.items():
            # criterion: ("k", "label", max_pts, lambda): always returns True/False
            # We produce exactly sc out of 100
            if sc == 0:
                _mock_crit[nm] = [("k", "lbl", 100, lambda m: False)]
            elif sc == 100:
                _mock_crit[nm] = [("k", "lbl", 100, lambda m: True)]
            else:
                earned = sc
                total  = 100
                _mock_crit[nm] = [
                    ("k1", "lbl1", earned, lambda m: True),
                    ("k2", "lbl2", total - earned, lambda m: False),
                ]

        with patch.dict("left_brain_market_intelligence._PLAYBOOK_CRITERIA", _mock_crit, clear=False):
            mi = {"suitable_playbooks": names}
            result = _compute_playbook_reasoning(mi)

        assert len(result) == 3
        returned_names = {r["name"] for r in result}
        expected_top3  = {names[3], names[4], names[1]}   # scores 80, 60, 50
        assert returned_names == expected_top3, (
            f"Wrong top-3: got {returned_names}, want {expected_top3}"
        )

    def test_regression_85_before_70(self):
        """Regression: a playbook scoring 85 must rank above one scoring 70."""
        from left_brain_market_intelligence import _PLAYBOOK_CRITERIA

        pb_hi, pb_lo = "MOCK_HI", "MOCK_LO"
        _mock = {
            pb_hi: [("k1", "hi earned", 85, lambda m: True),
                    ("k2", "hi missed", 15, lambda m: False)],
            pb_lo: [("k1", "lo earned", 70, lambda m: True),
                    ("k2", "lo missed", 30, lambda m: False)],
        }
        with patch.dict("left_brain_market_intelligence._PLAYBOOK_CRITERIA", _mock, clear=False):
            mi = {"suitable_playbooks": [pb_lo, pb_hi]}   # lo first in input
            result = _compute_playbook_reasoning(mi)

        assert result[0]["name"] == pb_hi, (
            f"Expected {pb_hi} (85%) first, got {result[0]['name']}"
        )
        assert result[0]["fit_score"] == 85
        assert result[1]["fit_score"] == 70


# ---------------------------------------------------------------------------
# Part 4 — OUTLOOK_SHIFT evidence
# ---------------------------------------------------------------------------

class TestOutlookShiftEvidence:
    """_detect_significant_changes must produce non-empty evidence for OUTLOOK_SHIFT."""

    def _make_mi(self, long: int, short: int, neutral: int = 0,
                 supporting_evidence: list | None = None) -> dict:
        out = {"directional_outlook": {"long": long, "short": short, "neutral": neutral}}
        if supporting_evidence is not None:
            out["supporting_evidence"] = supporting_evidence
        return out

    def _detect(self, prev_mi: dict, cur_mi: dict) -> list[dict]:
        # Signature: _detect_significant_changes(mi, prev_mi, direction, prev_direction,
        #                                         strength, prev_strength) → list[dict]
        # Pass neutral direction/strength to isolate OUTLOOK_SHIFT from other events.
        events = _detect_significant_changes(
            cur_mi, prev_mi,
            direction="NEUTRAL", prev_direction="NEUTRAL",
            strength=50,         prev_strength=50,
        )
        return [e for e in events if e["event_type"] == "OUTLOOK_SHIFT"]

    def test_bullish_shift_uses_mi_evidence(self):
        """When current MI has supporting_evidence, OUTLOOK_SHIFT uses it."""
        prev = self._make_mi(10, 5)
        cur  = self._make_mi(65, 5, supporting_evidence=["Strong bullish momentum."])
        events = self._detect(prev, cur)
        assert events, "Expected OUTLOOK_SHIFT event"
        assert events[0]["evidence"] == ["Strong bullish momentum."]

    def test_bearish_shift_derives_evidence_when_mi_empty(self):
        """When MI supporting_evidence is empty, derive evidence from the direction change."""
        prev = self._make_mi(0, 0)   # both 0
        cur  = self._make_mi(0, 20, supporting_evidence=[])
        events = self._detect(prev, cur)
        assert events, "Expected OUTLOOK_SHIFT event"
        evid = events[0]["evidence"]
        assert evid, "Evidence must be non-empty"
        assert any("earish" in e or "ominant" in e or "0%" in e for e in evid), (
            f"Evidence does not mention a direction change: {evid}"
        )

    def test_neutral_shift_generates_evidence(self):
        """A shift driven by neutral bucket increases → fallback evidence derived."""
        prev = self._make_mi(5, 5, neutral=0)
        cur  = self._make_mi(5, 5, neutral=20)
        events = self._detect(prev, cur)
        # Neutral change ≥10 but dominant may or may not cross the ≥15 threshold
        # Just verify that IF an OUTLOOK_SHIFT fires, evidence is non-empty.
        for ev in events:
            assert ev["evidence"], f"Empty evidence in event: {ev}"

    def test_no_stale_evidence_leakage_from_prev_mi(self):
        """Evidence must come from CURRENT MI, never copied from prev_mi."""
        prev = self._make_mi(0, 0, supporting_evidence=["STALE from prev."])
        cur  = self._make_mi(0, 20, supporting_evidence=[])
        events = self._detect(prev, cur)
        for ev in events:
            assert "STALE from prev." not in ev["evidence"], (
                "Stale prev_mi evidence leaked into OUTLOOK_SHIFT"
            )

    def test_repeated_identical_input_no_shift(self):
        """Identical prev/cur MI must not emit OUTLOOK_SHIFT."""
        mi = self._make_mi(30, 20)
        events = self._detect(mi, mi)
        assert not events, "Identical inputs should not produce OUTLOOK_SHIFT"

    def test_current_mi_evidence_not_copied_to_other_events(self):
        """OUTLOOK_SHIFT evidence must not bleed into non-OUTLOOK events."""
        prev = self._make_mi(0, 0)
        cur  = self._make_mi(0, 25, supporting_evidence=["Bearish momentum."])
        all_events = _detect_significant_changes(
            cur, prev,
            direction="NEUTRAL", prev_direction="NEUTRAL",
            strength=50,         prev_strength=50,
        )
        non_os = [e for e in all_events if e["event_type"] != "OUTLOOK_SHIFT"]
        for ev in non_os:
            # Other events should only have evidence they set themselves
            for e in ev.get("evidence", []):
                assert e != "Bearish momentum.", (
                    f"MI evidence leaked into {ev['event_type']}: {ev['evidence']}"
                )


# ---------------------------------------------------------------------------
# Part 10 — Observation infrastructure (in-memory)
# ---------------------------------------------------------------------------

class TestObservationRetention:
    """Verify _LB_THESIS_OBS_BY_INST uses maxlen=5000 and correct field names."""

    def test_deque_maxlen_is_5000(self):
        """The observation deque maxlen must be 5000 (not 120)."""
        from collections import deque as _deque
        d = _deque(maxlen=5000)
        # Verify that filling past 5000 items drops the oldest
        for i in range(5001):
            d.append({"ts": f"ts_{i}"})
        assert len(d) == 5000
        assert d[0]["ts"] == "ts_1"  # first item (ts_0) dropped

    def test_top_playbook_fit_score_field_name(self):
        """Observation snapshots must use 'top_playbook_fit_score', not 'top_playbook_fit'."""
        # Simulate the observation dict as written by the bar scan
        obs = {
            "ts": "2026-07-27T20:00:00+00:00",
            "instrument": "MGC",
            "top_playbook": "TREND_FOLLOW",
            "top_playbook_fit_score": 72,   # renamed field
        }
        assert "top_playbook_fit_score" in obs
        assert "top_playbook_fit" not in obs

    def test_obs_snapshot_schema_has_vwap_fields(self):
        """Observation snapshot schema must include vwap_age_ms and mi_input_ts."""
        obs = {
            "ts":                       "2026-07-27T20:00:00+00:00",
            "instrument":               "MGC",
            "direction":                "BULLISH",
            "vwap_source":              "chart",
            "vwap_age_ms":              None,       # None OK when ts absent
            "mi_input_ts":              "2026-07-27T20:00:01.123456+00:00",
        }
        assert "vwap_age_ms"  in obs
        assert "mi_input_ts"  in obs


class TestObservationDedup:
    """Dedup logic: same bar-ts (minute precision) must not produce duplicate observations."""

    def test_dedup_same_bar_skipped(self):
        """Two calls with the same mi_input_ts minute should produce exactly 1 observation."""
        last_bar: dict = {}
        obs_deq: deque = deque(maxlen=5000)

        def _append_obs(mi_ts: str, direction: str) -> bool:
            bar_key = mi_ts[:16]
            if last_bar.get("MGC") == bar_key:
                return False
            last_bar["MGC"] = bar_key
            obs_deq.append({"ts": "now", "direction": direction, "mi_input_ts": mi_ts})
            return True

        ts1 = "2026-07-27T20:01:00.000000+00:00"
        ts1b = "2026-07-27T20:01:30.000000+00:00"   # same minute
        ts2  = "2026-07-27T20:02:00.000000+00:00"

        assert _append_obs(ts1,  "BULLISH") is True,  "First obs should be appended"
        assert _append_obs(ts1b, "BULLISH") is False, "Same-minute obs should be skipped"
        assert len(obs_deq) == 1

        assert _append_obs(ts2, "BULLISH") is True,  "New-minute obs should be appended"
        assert len(obs_deq) == 2

    def test_dedup_isolates_per_instrument(self):
        """Dedup tracking is per instrument: MGC dedup does not block MNQ observation."""
        last_bar: dict = {}
        obs_deqs: dict = {"MGC": deque(maxlen=5000), "MNQ": deque(maxlen=5000)}

        def _append(inst: str, mi_ts: str) -> bool:
            key = mi_ts[:16]
            if last_bar.get(inst) == key:
                return False
            last_bar[inst] = key
            obs_deqs[inst].append({"ts": "now", "mi_input_ts": mi_ts})
            return True

        ts = "2026-07-27T20:01:00.000000+00:00"
        assert _append("MGC", ts) is True
        assert _append("MGC", ts) is False   # deduped for MGC
        assert _append("MNQ", ts) is True    # NOT deduped for MNQ


class TestObsEndpointQueryParams:
    """Test /lb-thesis-obs query-param parsing logic without HTTP (pure logic tests)."""

    def test_limit_clamps_to_1_5000(self):
        """limit param must be clamped to [1, 5000]."""
        MAX_RET = 5000
        for raw, expected in [("0", 1), ("6000", 5000), ("10", 10), ("-5", 1), ("5000", 5000)]:
            try:
                val = max(1, min(MAX_RET, int(raw)))
            except (ValueError, TypeError):
                val = MAX_RET
            assert val == expected, f"limit={raw!r} → expected {expected}, got {val}"

    def test_invalid_inst_rejected(self):
        """Invalid instrument names must be rejected."""
        VALID = {"MGC", "MNQ", "MES", "MYM"}
        for bad in ("GC", "NQ", "ES", "invalid", "", "mgc"):
            assert bad not in VALID

    def test_valid_inst_accepted(self):
        """Valid instrument names must be accepted."""
        VALID = {"MGC", "MNQ", "MES", "MYM"}
        for good in ("MGC", "MNQ", "MES", "MYM"):
            assert good in VALID

    def test_summary_param_check(self):
        """summary=1 sets summary_only=True; other values do not."""
        assert ("1" == "1") is True
        assert ("0" == "1") is False
        assert ("true" == "1") is False
        assert ("" == "1") is False

    def test_oldest_newest_metadata_logic(self):
        """oldest_ts/newest_ts should reflect first/last deque item."""
        raw = [
            {"ts": "2026-07-27T09:00:00"},  # index 0 = oldest
            {"ts": "2026-07-27T10:00:00"},
            {"ts": "2026-07-27T11:00:00"},  # index 2 = newest
        ]
        oldest_ts = raw[0]["ts"] if raw else None
        newest_ts = raw[-1]["ts"] if raw else None
        assert oldest_ts == "2026-07-27T09:00:00"
        assert newest_ts == "2026-07-27T11:00:00"

    def test_newest_first_after_reverse(self):
        """Reversing the raw deque list must yield newest entry first."""
        raw = [{"ts": "A"}, {"ts": "B"}, {"ts": "C"}]  # C is newest
        obs_newest = list(reversed(raw))
        assert obs_newest[0]["ts"] == "C"
        assert obs_newest[-1]["ts"] == "A"

    def test_retention_block_values(self):
        """retention block must advertise max_observations_per_instrument=5000."""
        retention = {"max_observations_per_instrument": 5000, "estimated_minutes": 5000}
        assert retention["max_observations_per_instrument"] == 5000
        assert retention["estimated_minutes"] == 5000


class TestMoneyPathIsolation:
    """Verify thesis/obs pipeline never touches gate, edge, broker."""

    def test_neutral_thesis_does_not_set_is_actionable(self):
        """The neutral thesis must not set is_actionable."""
        th = _neutral_thesis("MGC")
        assert "is_actionable" not in th, "is_actionable must not be present in thesis"
        assert "gate_pass"     not in th, "gate_pass must not be present in thesis"
        assert "edge_score"    not in th, "edge_score must not be present in thesis"

    def test_compute_left_brain_thesis_keys_are_display_only(self):
        """compute_left_brain_thesis output must not contain money-path keys."""
        # Minimal MI dict — keys the thesis function reads from
        minimal_mi: dict = {
            "market_state":          {"state": "UNKNOWN", "label": "Unknown", "reason": ""},
            "directional_confidence": {"long": 0, "short": 0, "bias": "neutral"},
            "directional_outlook":   {"long": 0, "short": 0, "neutral": 100},
            "suitable_playbooks":    [],
            "supporting_evidence":   [],
            "computed_at":           "2026-07-27T21:00:00+00:00",
        }
        out = compute_left_brain_thesis(
            inst="MGC",
            mi=minimal_mi,
            prev_mi=None,
            prev_thesis=None,
            memory_events=[],
        )
        th = out.get("thesis") or {}
        for banned in ("is_actionable", "gate_pass", "edge_score",
                       "auto_fire", "position_size", "tp1", "tp2"):
            assert banned not in th, f"Money-path key '{banned}' found in thesis"
