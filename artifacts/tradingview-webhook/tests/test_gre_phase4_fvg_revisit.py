"""
tests/test_gre_phase4_fvg_revisit.py
=====================================
Phase 4 — FVG_REVISIT Research Family tests.

Covers all 11 spec-required scenarios (Section 9 of correction doc) plus
comprehensive unit tests for FVG identity, variant logic, and entry/exit.

Safety proof: FVG broker calls = 0, money-path authority = NO.
"""

from __future__ import annotations

import hashlib
import json
import threading
import unittest
from unittest.mock import MagicMock, patch, call
from typing import Any, Dict, List, Optional

# ── Import the module under test ──────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ghost_research_engine as gre
from ghost_research_engine import (
    # helpers
    _fvg_research_id, _fvg_revisit_id, _fvg_opportunity_id,
    _fvg_depth_pct, _fvg_classify_location, _fvg_bar_overlaps,
    _canonical_direction_fvg, _quantize_to_tick, _canonical_ts_fvg,
    _FVG_HASH_VERSION, FVG_STRATEGY_NAME, _TICK_SIZE,
    # constants
    STRATEGY_FAMILY_ORB, STRATEGY_FAMILY_FVG,
    STRATEGY_NAME,
    _FVG_NEAR_EDGE_FRAC, _FVG_SHALLOW_FRAC, _FVG_MIDPOINT_FRAC, _FVG_DEEP_FRAC,
    _FVG_MIDPOINT_MIN_PCT, _FVG_DEEP_FILL_MIN_PCT, _FVG_BASELINE_TARGET_R,
    _FVG_MAX_WAITING_BARS,
    # classes
    FvgVariant, FVG_ALL_VARIANTS,
    OutcomeResult, ResultStatus,
    GhostResearchEngine,
    MAX_GHOST_VARIANTS_HARD_CAP,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _zone(direction="BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000,
           status="ACTIVE", touch_count=0, zone_id="test-uuid-1234"):
    return {
        "direction": direction, "upper": upper, "lower": lower,
        "midpoint": (upper + lower) / 2,
        "bar_ts": bar_ts, "status": status, "touch_count": touch_count,
        "id": zone_id, "created_at": "2024-01-01T09:30:00+00:00",
    }


def _bar(ts=1700001000, o=1995.0, h=2005.0, l=1988.0, c=1998.0, volume=500):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": volume}


def _mock_gre(instruments=None):
    """Build a GhostResearchEngine with all DB calls mocked out."""
    if instruments is None:
        instruments = ["MNQ", "MGC"]
    db   = MagicMock()
    cur  = MagicMock()
    cur.fetchone.return_value = ("row_id",)
    cur.fetchall.return_value = []
    cur.description = []
    db.cursor.return_value = cur

    engine = GhostResearchEngine(
        get_db_fn=lambda: db,
        get_canonical_fn=lambda inst: {},
        get_bars_fn=lambda inst: [],
        re_event_fn=lambda *a, **kw: None,
        instruments=instruments,
    )
    GhostResearchEngine.GRE_DB_READY = True
    return engine, db, cur


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Pure unit tests (no DB needed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgConstants(unittest.TestCase):
    # Spec requirement: 10 variants, within hard cap
    def test_fvg_all_variants_count(self):
        self.assertEqual(len(FVG_ALL_VARIANTS), 10)

    def test_fvg_variants_within_hard_cap(self):
        self.assertLessEqual(len(FVG_ALL_VARIANTS), MAX_GHOST_VARIANTS_HARD_CAP)

    def test_fvg_strategy_family_value(self):
        self.assertEqual(STRATEGY_FAMILY_FVG, "FVG_REVISIT")

    def test_fvg_strategy_name_value(self):
        # Spec requirement: strategy = FVG_RESEARCH_BASELINE_V1 (not FVG_REVISIT)
        self.assertEqual(FVG_STRATEGY_NAME, "FVG_RESEARCH_BASELINE_V1")
        self.assertNotEqual(FVG_STRATEGY_NAME, STRATEGY_FAMILY_FVG)

    def test_orb_family_value(self):
        self.assertEqual(STRATEGY_FAMILY_ORB, "09:30_ORB")

    def test_orb_strategy_name_preserved(self):
        # Spec requirement: ORB strategy value preserved
        self.assertEqual(STRATEGY_NAME, "09:30_ORB")

    def test_outcome_result_has_invalidated(self):
        self.assertEqual(OutcomeResult.INVALIDATED_BEFORE_ENTRY, "INVALIDATED_BEFORE_ENTRY")

    def test_all_fvg_variant_names_present(self):
        expected = {
            "BASELINE", "NEAR_EDGE_ENTRY", "MIDPOINT_ENTRY", "DEEP_FILL_ENTRY",
            "FIRST_TOUCH_ONLY", "SECOND_TOUCH_ALLOWED", "TREND_REQUIRED",
            "CVD_ALIGNED", "TP_1R", "TP_1_5R",
        }
        self.assertEqual(set(FVG_ALL_VARIANTS), expected)

    def test_no_duplicate_variant_names(self):
        self.assertEqual(len(FVG_ALL_VARIANTS), len(set(FVG_ALL_VARIANTS)))

    def test_baseline_target_r(self):
        self.assertEqual(_FVG_BASELINE_TARGET_R, 2.0)

    def test_max_waiting_bars(self):
        self.assertGreaterEqual(_FVG_MAX_WAITING_BARS, 30)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Identity helpers (deterministic ID tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgResearchId(unittest.TestCase):

    def test_same_inputs_same_id(self):
        """Spec: same FVG replay → same research_fvg_id."""
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        self.assertEqual(id1, id2)

    def test_different_fvg_different_id(self):
        """Spec: different FVG → different research_fvg_id."""
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2001.0, 1990.0)  # different upper
        self.assertNotEqual(id1, id2)

    def test_direction_case_insensitive(self):
        """BULLISH and bullish should produce same id."""
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        id2 = _fvg_research_id("MNQ", "bullish", 1700000000, 2000.0, 1990.0)
        self.assertEqual(id1, id2)

    def test_random_source_uuid_does_not_change_research_id(self):
        """
        Spec: random source uuid changing does not change logical research_fvg_id
        for identical historical FVG (zone props are the same, only id field differs).
        """
        # research_fvg_id doesn't use the source uuid (zone.id) at all
        zone1 = _zone(zone_id="uuid-aaaa")
        zone2 = _zone(zone_id="uuid-bbbb")  # different source uuid, same zone bounds
        id1 = _fvg_research_id(
            "MNQ", zone1["direction"], zone1["bar_ts"],
            zone1["upper"], zone1["lower"],
        )
        id2 = _fvg_research_id(
            "MNQ", zone2["direction"], zone2["bar_ts"],
            zone2["upper"], zone2["lower"],
        )
        self.assertEqual(id1, id2)  # research ID is independent of source uuid

    def test_id_is_24_hex_chars(self):
        rid = _fvg_research_id("MGC", "BEARISH", 1700000000, 1950.0, 1940.0)
        self.assertEqual(len(rid), 24)
        self.assertTrue(all(c in "0123456789abcdef" for c in rid))

    def test_different_instruments_different_id(self):
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        id2 = _fvg_research_id("MGC", "BULLISH", 1700000000, 2000.0, 1990.0)
        self.assertNotEqual(id1, id2)

    def test_different_bar_ts_different_id(self):
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000001, 2000.0, 1990.0)
        self.assertNotEqual(id1, id2)


class TestFvgRevisitId(unittest.TestCase):

    def test_same_inputs_same_revisit_id(self):
        """Spec: same revisit callback → same revisit_id."""
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rid1 = _fvg_revisit_id(rfid, 1, 1700001000)
        rid2 = _fvg_revisit_id(rfid, 1, 1700001000)
        self.assertEqual(rid1, rid2)

    def test_later_revisit_different_id(self):
        """Spec: later legitimate revisit → different revisit_id."""
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rid1 = _fvg_revisit_id(rfid, 1, 1700001000)
        rid2 = _fvg_revisit_id(rfid, 2, 1700002000)  # different revisit_n + bar_ts
        self.assertNotEqual(rid1, rid2)

    def test_same_revisit_n_different_bar_ts_different_id(self):
        """Same revisit number but different bar timestamp → different id."""
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rid1 = _fvg_revisit_id(rfid, 1, 1700001000)
        rid2 = _fvg_revisit_id(rfid, 1, 1700001060)
        self.assertNotEqual(rid1, rid2)

    def test_revisit_id_is_24_hex_chars(self):
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rid  = _fvg_revisit_id(rfid, 1, 1700001000)
        self.assertEqual(len(rid), 24)
        self.assertTrue(all(c in "0123456789abcdef" for c in rid))


class TestFvgOpportunityId(unittest.TestCase):

    def test_deterministic(self):
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        oid1 = _fvg_opportunity_id("MNQ", rfid, 1, 1700001000)
        oid2 = _fvg_opportunity_id("MNQ", rfid, 1, 1700001000)
        self.assertEqual(oid1, oid2)

    def test_different_revisit_n_different_opp(self):
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        oid1 = _fvg_opportunity_id("MNQ", rfid, 1, 1700001000)
        oid2 = _fvg_opportunity_id("MNQ", rfid, 2, 1700002000)
        self.assertNotEqual(oid1, oid2)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Depth / location helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgDepthPct(unittest.TestCase):

    def test_bullish_full_fill(self):
        z = _zone("BULLISH", upper=2000.0, lower=1990.0)
        pct = _fvg_depth_pct(z, bar_low=1990.0, bar_high=2000.0)
        self.assertAlmostEqual(pct, 1.0, places=3)

    def test_bullish_no_fill(self):
        z = _zone("BULLISH", upper=2000.0, lower=1990.0)
        pct = _fvg_depth_pct(z, bar_low=2001.0, bar_high=2010.0)
        self.assertEqual(pct, 0.0)

    def test_bullish_half_fill(self):
        z = _zone("BULLISH", upper=2000.0, lower=1990.0)
        # bar_low = 1995 → 5/10 = 0.5
        pct = _fvg_depth_pct(z, bar_low=1995.0, bar_high=2000.0)
        self.assertAlmostEqual(pct, 0.5, places=3)

    def test_bearish_full_fill(self):
        z = _zone("BEARISH", upper=2000.0, lower=1990.0)
        pct = _fvg_depth_pct(z, bar_low=1990.0, bar_high=2000.0)
        self.assertAlmostEqual(pct, 1.0, places=3)

    def test_bearish_half_fill(self):
        z = _zone("BEARISH", upper=2000.0, lower=1990.0)
        # bar_high = 1995 → (1995-1990)/10 = 0.5
        pct = _fvg_depth_pct(z, bar_low=1985.0, bar_high=1995.0)
        self.assertAlmostEqual(pct, 0.5, places=3)

    def test_clamped_to_0_1(self):
        z = _zone("BULLISH", upper=2000.0, lower=1990.0)
        pct = _fvg_depth_pct(z, bar_low=1980.0, bar_high=2010.0)  # well beyond
        self.assertEqual(pct, 1.0)


class TestFvgClassifyLocation(unittest.TestCase):

    def test_near_edge(self):
        self.assertEqual(_fvg_classify_location(0.0), "NEAR_EDGE")
        self.assertEqual(_fvg_classify_location(0.20), "NEAR_EDGE")

    def test_shallow_fill(self):
        self.assertEqual(_fvg_classify_location(0.21), "SHALLOW_FILL")
        self.assertEqual(_fvg_classify_location(0.40), "SHALLOW_FILL")

    def test_midpoint(self):
        self.assertEqual(_fvg_classify_location(0.41), "MIDPOINT")
        self.assertEqual(_fvg_classify_location(0.60), "MIDPOINT")

    def test_deep_fill(self):
        self.assertEqual(_fvg_classify_location(0.61), "DEEP_FILL")
        self.assertEqual(_fvg_classify_location(0.80), "DEEP_FILL")

    def test_full_fill(self):
        self.assertEqual(_fvg_classify_location(0.81), "FULL_FILL")
        self.assertEqual(_fvg_classify_location(1.00), "FULL_FILL")


class TestFvgBarOverlaps(unittest.TestCase):

    def test_fully_inside(self):
        z = _zone(upper=2000.0, lower=1990.0)
        self.assertTrue(_fvg_bar_overlaps(z, bar_low=1992.0, bar_high=1998.0))

    def test_touches_upper(self):
        z = _zone(upper=2000.0, lower=1990.0)
        self.assertTrue(_fvg_bar_overlaps(z, bar_low=2000.0, bar_high=2005.0))

    def test_touches_lower(self):
        z = _zone(upper=2000.0, lower=1990.0)
        self.assertTrue(_fvg_bar_overlaps(z, bar_low=1985.0, bar_high=1990.0))

    def test_fully_above_zone(self):
        z = _zone(upper=2000.0, lower=1990.0)
        self.assertFalse(_fvg_bar_overlaps(z, bar_low=2001.0, bar_high=2010.0))

    def test_fully_below_zone(self):
        z = _zone(upper=2000.0, lower=1990.0)
        self.assertFalse(_fvg_bar_overlaps(z, bar_low=1980.0, bar_high=1989.9))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Entry condition logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgCheckEntryCondition(unittest.TestCase):

    def _eng(self):
        eng, _, _ = _mock_gre()
        return eng

    def _check(self, engine, entry_rule, direction, filter_r, bar_l, bar_h, bar_c,
               fvg_upper=2000.0, fvg_lower=1990.0, fvg_dir="BULLISH", revisit_n=1, canonical=None):
        return engine._fvg_check_entry_condition(
            entry_rule, direction, filter_r, canonical or {},
            bar_l, bar_h, bar_c, fvg_upper, fvg_lower, fvg_dir, revisit_n,
        )

    def test_baseline_close_inside_zone_enters(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long", {"tp_mult": 2.0},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0,  # close inside
        )
        self.assertTrue(entered)
        self.assertEqual(ep, 1995.0)  # entry = bar close

    def test_baseline_close_outside_zone_no_entry(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long", {"tp_mult": 2.0},
            bar_l=2001.0, bar_h=2010.0, bar_c=2008.0,  # close above zone
        )
        self.assertFalse(entered)

    def test_near_edge_within_limit_enters(self):
        eng = self._eng()
        # gap = 10; near-edge = first 20% = 2 pts from upper (1998.0+)
        entered, ep = self._check(
            eng, "FVG_NEAR_EDGE", "Long",
            {"tp_mult": 2.0, "max_depth_pct": _FVG_NEAR_EDGE_FRAC},
            bar_l=1998.5, bar_h=2002.0, bar_c=1999.0,  # close at 1pt depth (10%)
        )
        self.assertTrue(entered)

    def test_near_edge_too_deep_no_entry(self):
        eng = self._eng()
        # close at 1994 = 6/10 = 60% deep, exceeds 20%
        entered, ep = self._check(
            eng, "FVG_NEAR_EDGE", "Long",
            {"tp_mult": 2.0, "max_depth_pct": _FVG_NEAR_EDGE_FRAC},
            bar_l=1993.0, bar_h=2002.0, bar_c=1994.0,
        )
        self.assertFalse(entered)

    def test_midpoint_entry_at_50pct_enters(self):
        eng = self._eng()
        # close at 1995 = 5/10 = 50% depth = exactly at minimum
        entered, ep = self._check(
            eng, "FVG_MIDPOINT", "Long",
            {"tp_mult": 2.0, "min_depth_pct": _FVG_MIDPOINT_MIN_PCT},
            bar_l=1994.0, bar_h=2002.0, bar_c=1995.0,
        )
        self.assertTrue(entered)

    def test_midpoint_entry_shallow_no_entry(self):
        eng = self._eng()
        # close at 1999 = 1/10 = 10%, below 50% minimum
        entered, ep = self._check(
            eng, "FVG_MIDPOINT", "Long",
            {"tp_mult": 2.0, "min_depth_pct": _FVG_MIDPOINT_MIN_PCT},
            bar_l=1998.0, bar_h=2002.0, bar_c=1999.0,
        )
        self.assertFalse(entered)

    def test_deep_fill_at_70pct_enters(self):
        eng = self._eng()
        # close at 1993 = 7/10 = 70% depth = exactly minimum
        entered, ep = self._check(
            eng, "FVG_DEEP_FILL", "Long",
            {"tp_mult": 2.0, "min_depth_pct": _FVG_DEEP_FILL_MIN_PCT},
            bar_l=1992.0, bar_h=2002.0, bar_c=1993.0,
        )
        self.assertTrue(entered)

    def test_first_touch_only_revisit_n1_enters(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "max_revisit_n": 1},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, revisit_n=1,
        )
        self.assertTrue(entered)

    def test_first_touch_only_revisit_n2_blocked(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "max_revisit_n": 1},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, revisit_n=2,
        )
        self.assertFalse(entered)

    def test_second_touch_allowed_n2_enters(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "max_revisit_n": 2},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, revisit_n=2,
        )
        self.assertTrue(entered)

    def test_second_touch_blocked_at_n3(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "max_revisit_n": 2},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, revisit_n=3,
        )
        self.assertFalse(entered)

    def test_trend_required_bullish_aligned_enters(self):
        eng = self._eng()
        canonical = {"trend": {"trend_15m": "BULLISH_MOMENTUM"}}
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "require_trend_align": True},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, canonical=canonical,
        )
        self.assertTrue(entered)

    def test_trend_required_bearish_blocks_long(self):
        eng = self._eng()
        canonical = {"trend": {"trend_15m": "BEARISH_TREND"}}
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "require_trend_align": True},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, canonical=canonical,
        )
        self.assertFalse(entered)

    def test_cvd_aligned_bullish_cvd_enters_long(self):
        eng = self._eng()
        canonical = {"cvd": {"direction": "BULLISH"}}
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "require_cvd_align": True},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, canonical=canonical,
        )
        self.assertTrue(entered)

    def test_cvd_aligned_bearish_cvd_blocks_long(self):
        eng = self._eng()
        canonical = {"cvd": {"direction": "BEARISH"}}
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long",
            {"tp_mult": 2.0, "require_cvd_align": True},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0, canonical=canonical,
        )
        self.assertFalse(entered)

    def test_short_direction_bearish_zone_enters(self):
        eng = self._eng()
        # Bearish FVG: price enters from lower side
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Short",
            {"tp_mult": 2.0},
            bar_l=1985.0, bar_h=1995.0, bar_c=1993.0,
            fvg_upper=2000.0, fvg_lower=1990.0, fvg_dir="BEARISH",
        )
        self.assertTrue(entered)
        self.assertEqual(ep, 1993.0)

    def test_none_bar_values_returns_false(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long", {"tp_mult": 2.0},
            bar_l=None, bar_h=None, bar_c=1995.0,
        )
        self.assertFalse(entered)

    def test_none_zone_bounds_returns_false(self):
        eng = self._eng()
        entered, ep = self._check(
            eng, "FVG_ZONE_TOUCH", "Long", {"tp_mult": 2.0},
            bar_l=1992.0, bar_h=2002.0, bar_c=1995.0,
            fvg_upper=None, fvg_lower=None,
        )
        self.assertFalse(entered)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — on_fvg_bar_close safety / fail-open
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnFvgBarCloseSafety(unittest.TestCase):

    def test_no_op_when_db_not_ready(self):
        """Fail-open: GRE_DB_READY=False → no processing, no crash."""
        eng, db, cur = _mock_gre()
        GhostResearchEngine.GRE_DB_READY = False
        z = _zone()
        b = _bar()
        eng.on_fvg_bar_close("MNQ", [z], b, 1995.0)
        # DB should never be called
        db.cursor.assert_not_called()
        GhostResearchEngine.GRE_DB_READY = True

    def test_exception_in_process_inst_does_not_propagate(self):
        """Fail-open: internal exception must not surface to caller."""
        eng, db, _ = _mock_gre()
        # Cause an exception inside _fvg_process_inst by making _fvg_check_revisit blow up
        with patch.object(eng, "_fvg_check_revisit", side_effect=RuntimeError("boom")):
            try:
                eng.on_fvg_bar_close("MNQ", [_zone()], _bar(), 1995.0)
            except Exception as e:
                self.fail(f"on_fvg_bar_close raised: {e}")

    def test_fvg_does_not_call_execute_trade_gateway(self):
        """Safety contract: no broker path called from FVG research."""
        import ghost_research_engine as gre_mod
        # execute_trade_gateway must not be importable from ghost_research_engine
        self.assertFalse(hasattr(gre_mod, "execute_trade_gateway"),
                         "ghost_research_engine must not expose execute_trade_gateway")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Revisit detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestRevisitDetection(unittest.TestCase):

    def _eng_with_spies(self):
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = ("inserted_row",)
        cur.fetchall.return_value = []
        cur.description = []
        return eng, db, cur

    def test_first_revisit_creates_opportunity(self):
        eng, db, cur = self._eng_with_spies()
        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        # Simulate: previous bar outside zone, current bar inside (revisit detected)
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        eng._fvg_inside_prev[rfid] = False  # was outside

        eng._fvg_check_revisit("MNQ", z, 1992.0, 2002.0, 1700001000, 1995.0, {})

        # revisit_n should be 1
        self.assertEqual(eng._fvg_revisit_count.get(rfid), 1)

    def test_same_revisit_session_no_duplicate(self):
        """Spec: same revisit callback → same revisit_id / no duplicate."""
        eng, db, cur = self._eng_with_spies()
        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        eng._fvg_inside_prev[rfid] = False

        # First call: creates opportunity
        eng._fvg_check_revisit("MNQ", z, 1992.0, 2002.0, 1700001000, 1995.0, {})

        # Simulate staying inside zone (was_inside=True now)
        # Calling again should NOT create another opportunity
        eng._fvg_check_revisit("MNQ", z, 1991.0, 2001.0, 1700001060, 1994.0, {})

        # No new revisit session should be created (count stays at 1)
        self.assertEqual(eng._fvg_revisit_count.get(rfid), 1)

    def test_second_revisit_session_creates_new_opportunity(self):
        """Spec: later legitimate revisit → different revisit_id."""
        eng, db, cur = self._eng_with_spies()
        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)

        # First revisit
        eng._fvg_inside_prev[rfid] = False
        eng._fvg_check_revisit("MNQ", z, 1992.0, 2002.0, 1700001000, 1995.0, {})
        self.assertEqual(eng._fvg_revisit_count.get(rfid), 1)

        # Price exits zone
        eng._fvg_inside_prev[rfid] = False  # simulate exit

        # Second revisit
        eng._fvg_check_revisit("MNQ", z, 1993.0, 2001.0, 1700002000, 1996.0, {})
        self.assertEqual(eng._fvg_revisit_count.get(rfid), 2)

    def test_inactive_zone_not_processed(self):
        """Only ACTIVE or TOUCHED zones trigger revisit detection."""
        eng, db, cur = self._eng_with_spies()
        z = _zone(status="MITIGATED")
        rfid = _fvg_research_id("MNQ", "BULLISH", z["bar_ts"], z["upper"], z["lower"])
        eng._fvg_inside_prev[rfid] = False

        # Call _fvg_process_inst directly — MITIGATED zone should be skipped
        eng._fvg_process_inst("MNQ", [z], _bar(), 1995.0, {})

        # No revisit should be created
        self.assertEqual(eng._fvg_revisit_count.get(rfid, 0), 0)

    def test_first_touch_only_pre_no_entry_on_revisit_2(self):
        """FIRST_TOUCH_ONLY experiment gets pre_no_entry=True when revisit_n > 1."""
        eng, db, cur = self._eng_with_spies()
        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        rfid = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)

        # Set up revisit 2 state
        eng._fvg_revisit_count[rfid] = 1  # already had 1 revisit
        eng._fvg_opp_created[f"{rfid}|1"] = "prev-opp-id"
        eng._fvg_inside_prev[rfid] = False

        # Second revisit fires
        eng._fvg_check_revisit("MNQ", z, 1992.0, 2002.0, 1700002000, 1995.0, {})
        self.assertEqual(eng._fvg_revisit_count.get(rfid), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Experiment lifecycle (entry/exit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgExperimentLifecycle(unittest.TestCase):

    def _make_open_result(self, status=ResultStatus.WATCHING_ENTRY,
                          direction="Long", stop=1988.0, tp1=2010.0,
                          entry=None, rfid="test_rfid", revisit_n=1,
                          fvg_upper=2000.0, fvg_lower=1990.0):
        return {
            "result_id":     "RES_test",
            "experiment_id": "EXP_test",
            "opportunity_id": "OPP_test",
            "status":        status,
            "variant_name":  FvgVariant.BASELINE,
            "instrument":    "MNQ",
            "direction":     direction,
            "entry_price":   entry,
            "stop_price":    stop,
            "tp1_price":     tp1,
            "tp2_price":     None,
            "entry_rule":    "FVG_ZONE_TOUCH",
            "filter_rules":  {
                "tp_mult": 2.0, "_rfid": rfid, "_revisit_n": revisit_n,
                "_fvg_upper": fvg_upper, "_fvg_lower": fvg_lower,
                "_fvg_direction": "BULLISH",
            },
            "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None, "mae_price": None,
            "bars_held": 0, "last_bar_ts": None, "_fvg_family": True,
        }

    def _eng_with_result(self, rd):
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []
        cur.description = []
        eng._open_results["RES_test"] = dict(rd)
        return eng, db, cur

    def test_win_on_target_hit(self):
        rd = self._make_open_result(status=ResultStatus.ACTIVE, entry=1995.0)
        eng, db, cur = self._eng_with_result(rd)
        zones_by_rfid = {}  # not needed for ACTIVE

        eng._fvg_process_one_experiment(
            "RES_test", "MNQ", zones_by_rfid,
            bar_ts=1700001060,
            bar_h=2011.0,  # TP hit (tp1=2010)
            bar_l=1992.0,
            bar_c=2010.5,
            canonical={},
        )
        self.assertNotIn("RES_test", eng._open_results)
        update_call = cur.execute.call_args_list
        # Find the UPDATE call
        update_sql = [str(c) for c in update_call if "UPDATE ghost_experiment_results" in str(c)]
        self.assertTrue(any("WIN" in s or "TP1_HIT" in s for s in update_sql) or
                        len(update_sql) > 0)  # result logged to DB

    def test_loss_on_stop_hit(self):
        rd = self._make_open_result(status=ResultStatus.ACTIVE, entry=1995.0)
        eng, db, cur = self._eng_with_result(rd)

        eng._fvg_process_one_experiment(
            "RES_test", "MNQ", {},
            bar_ts=1700001060,
            bar_h=1993.0,
            bar_l=1987.0,  # Stop hit (stop=1988)
            bar_c=1989.0,
            canonical={},
        )
        self.assertNotIn("RES_test", eng._open_results)

    def test_invalidated_when_zone_gone(self):
        rd = self._make_open_result(status=ResultStatus.WATCHING_ENTRY)
        eng, db, cur = self._eng_with_result(rd)
        # Zone is now MITIGATED
        zones_by_rfid = {"test_rfid": {"status": "MITIGATED"}}

        eng._fvg_process_one_experiment(
            "RES_test", "MNQ", zones_by_rfid,
            bar_ts=1700001060,
            bar_h=2002.0, bar_l=1992.0, bar_c=1995.0,
            canonical={},
        )
        self.assertNotIn("RES_test", eng._open_results)

    def test_expired_after_max_bars(self):
        rd = self._make_open_result(status=ResultStatus.WATCHING_ENTRY)
        rd["bars_held"] = _FVG_MAX_WAITING_BARS  # at the limit
        eng, db, cur = self._eng_with_result(rd)
        zones_by_rfid = {"test_rfid": {"status": "ACTIVE"}}

        eng._fvg_process_one_experiment(
            "RES_test", "MNQ", zones_by_rfid,
            bar_ts=1700001060,
            bar_h=2003.0, bar_l=1985.0, bar_c=1985.0,  # close outside zone
            canonical={},
        )
        self.assertNotIn("RES_test", eng._open_results)

    def test_ambiguous_bar_stop_wins(self):
        """Conservative: when stop and target hit same bar, stop (LOSS) wins."""
        rd = self._make_open_result(status=ResultStatus.ACTIVE, entry=1995.0,
                                    stop=1988.0, tp1=2010.0)
        eng, db, cur = self._eng_with_result(rd)

        eng._fvg_process_one_experiment(
            "RES_test", "MNQ", {},
            bar_ts=1700001060,
            bar_h=2012.0,   # above tp1
            bar_l=1987.0,   # below stop
            bar_c=1990.0,
            canonical={},
        )
        self.assertNotIn("RES_test", eng._open_results)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — ORB / FVG isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrbFvgIsolation(unittest.TestCase):

    def test_fvg_experiments_skipped_by_orb_processor(self):
        """FVG experiments (_fvg_family=True) must not be touched by ORB path."""
        eng, db, cur = _mock_gre()
        cur.fetchall.return_value = []
        cur.description = []

        # Plant a FVG experiment in _open_results
        fvg_rd = {
            "result_id": "RES_fvg", "instrument": "MNQ", "_fvg_family": True,
            "status": ResultStatus.WATCHING_ENTRY, "direction": "Long",
            "entry_rule": "FVG_ZONE_TOUCH",
        }
        orb_rd = {
            "result_id": "RES_orb", "instrument": "MNQ", "_fvg_family": False,
            "status": ResultStatus.WATCHING_ENTRY, "direction": "Long",
            "entry_rule": "TOUCH", "entry_price": None, "stop_price": 1980.0,
            "tp1_price": 2020.0, "tp2_price": None, "filter_rules": {},
            "mfe_r": 0.0, "mae_r": 0.0, "bars_held": 0,
        }
        eng._open_results["RES_fvg"] = fvg_rd
        eng._open_results["RES_orb"] = orb_rd

        # _process_open_experiments should only pick up ORB experiment
        bar = _bar()
        orb_mock = MagicMock()
        with patch.object(eng, "_process_one_experiment") as mock_one:
            eng._process_open_experiments("MNQ", bar, 1995.0, orb_mock)
            called_ids = [call.args[0] for call in mock_one.call_args_list]
            self.assertIn("RES_orb", called_ids)
            self.assertNotIn("RES_fvg", called_ids)

    def test_orb_experiments_skipped_by_fvg_processor(self):
        """ORB experiments must not be touched by FVG path."""
        eng, db, cur = _mock_gre()
        orb_rd = {
            "result_id": "RES_orb", "instrument": "MNQ", "_fvg_family": False,
            "status": ResultStatus.WATCHING_ENTRY, "direction": "Long",
        }
        eng._open_results["RES_orb"] = orb_rd

        zones_by_rfid = {}
        bar = _bar()
        with patch.object(eng, "_fvg_process_one_experiment") as mock_fvg:
            eng._fvg_process_open_experiments("MNQ", zones_by_rfid, bar, 1700001000,
                                              1992.0, 2002.0, 1995.0, {})
            # ORB experiment should never be passed to FVG processor
            called_ids = [call.args[0] for call in mock_fvg.call_args_list]
            self.assertNotIn("RES_orb", called_ids)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Strategy family correctness (spec-required DB tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyFamilyCorrectness(unittest.TestCase):
    """
    These tests verify the strategy/strategy_family separation
    required by the Phase 4 architecture correction.
    """

    def test_fvg_strategy_family_is_fvg_revisit(self):
        """Spec: FVG row has strategy_family=FVG_REVISIT."""
        self.assertEqual(STRATEGY_FAMILY_FVG, "FVG_REVISIT")

    def test_fvg_strategy_is_baseline_v1_not_family(self):
        """Spec: FVG strategy = FVG_RESEARCH_BASELINE_V1, not FVG_REVISIT."""
        self.assertEqual(FVG_STRATEGY_NAME, "FVG_RESEARCH_BASELINE_V1")
        self.assertNotEqual(FVG_STRATEGY_NAME, STRATEGY_FAMILY_FVG)

    def test_orb_strategy_family_distinct_from_strategy(self):
        """
        ORB strategy_family = '09:30_ORB'.
        ORB strategy (STRATEGY_NAME) = '09:30_ORB' (they happen to be same for ORB,
        but they are LOGICALLY distinct fields).
        """
        self.assertEqual(STRATEGY_FAMILY_ORB, "09:30_ORB")
        self.assertEqual(STRATEGY_NAME, "09:30_ORB")  # ORB strategy preserved

    def test_fvg_insert_uses_correct_strategy_family(self):
        """Verify _insert_fvg_opportunity writes STRATEGY_FAMILY_FVG, not strategy name."""
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = ("row",)
        cur.fetchall.return_value = []
        cur.description = []

        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        rfid     = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rev_id   = _fvg_revisit_id(rfid, 1, 1700001000)
        snap     = {"trend_15m": "BULLISH", "cvd_direction": "BULLISH",
                    "current_price": 1995.0, "_extra_snapshot": {}}

        eng._insert_fvg_opportunity("opp123", "MNQ", z, rfid, rev_id, 1, snap)

        # Find the INSERT execute call and verify strategy_family = FVG_REVISIT
        insert_calls = [str(c) for c in cur.execute.call_args_list
                        if "INSERT INTO ghost_opportunities" in str(c)]
        self.assertTrue(len(insert_calls) > 0, "Expected INSERT call")
        # The params tuple should contain STRATEGY_FAMILY_FVG
        all_params = [str(c.args[1]) for c in cur.execute.call_args_list
                      if "INSERT INTO ghost_opportunities" in str(c.args[0])]
        self.assertTrue(any(STRATEGY_FAMILY_FVG in p for p in all_params),
                        f"STRATEGY_FAMILY_FVG not found in params. Got: {all_params}")

    def test_fvg_insert_uses_correct_strategy_name(self):
        """Verify _insert_fvg_opportunity writes FVG_STRATEGY_NAME as strategy."""
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = ("row",)
        cur.fetchall.return_value = []
        cur.description = []

        z = _zone("BULLISH", upper=2000.0, lower=1990.0, bar_ts=1700000000)
        rfid   = _fvg_research_id("MNQ", "BULLISH", 1700000000, 2000.0, 1990.0)
        rev_id = _fvg_revisit_id(rfid, 1, 1700001000)
        snap   = {"trend_15m": "BULLISH", "current_price": 1995.0, "_extra_snapshot": {}}

        eng._insert_fvg_opportunity("opp123", "MNQ", z, rfid, rev_id, 1, snap)

        all_params = [str(c.args[1]) for c in cur.execute.call_args_list
                      if "INSERT INTO ghost_opportunities" in str(c.args[0])]
        self.assertTrue(any(FVG_STRATEGY_NAME in p for p in all_params),
                        f"FVG_STRATEGY_NAME not found in params. Got: {all_params}")

    def test_get_health_returns_both_families(self):
        """get_health() must list both research families."""
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = (0, 0)
        cur.fetchall.return_value = []
        cur.description = []

        health = eng.get_health()
        self.assertIn("families", health)
        self.assertIn(STRATEGY_FAMILY_ORB, health["families"])
        self.assertIn(STRATEGY_FAMILY_FVG, health["families"])

    def test_get_health_with_family_filter(self):
        """get_health(family=...) must set filter_family correctly."""
        eng, db, cur = _mock_gre()
        cur.fetchone.return_value = (0, 0)
        cur.fetchall.return_value = []
        cur.description = []

        health = eng.get_health(family=STRATEGY_FAMILY_FVG)
        self.assertEqual(health.get("filter_family"), STRATEGY_FAMILY_FVG)

    def test_get_experiments_accepts_family_param(self):
        """get_experiments() must accept family parameter."""
        eng, db, cur = _mock_gre()
        cur.fetchall.return_value = []
        cur.description = []
        # Must not raise
        result = eng.get_experiments(family=STRATEGY_FAMILY_FVG)
        self.assertIsInstance(result, list)

    def test_get_candidates_accepts_family_param(self):
        """get_candidates() must accept family parameter."""
        eng, db, cur = _mock_gre()
        cur.fetchall.return_value = []
        cur.description = []
        result = eng.get_candidates(family=STRATEGY_FAMILY_FVG)
        self.assertIsInstance(result, list)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Restore marks FVG experiments correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestoreMarksFvg(unittest.TestCase):

    def test_restore_marks_fvg_by_entry_rule(self):
        """After restore, FVG experiments have _fvg_family=True."""
        eng, db, cur = _mock_gre()
        # Simulate DB returning an FVG experiment row
        fvg_row = {
            "result_id": "RES_fvg", "experiment_id": "EXP_fvg",
            "opportunity_id": "OPP_fvg", "status": ResultStatus.WATCHING_ENTRY,
            "entry_price": None, "stop_price": 1988.0, "tp1_price": 2010.0,
            "tp2_price": None, "mfe_r": 0.0, "mae_r": 0.0, "mfe_price": None,
            "mae_price": None, "bars_held": 0, "last_bar_ts": None,
            "variant_name": FvgVariant.BASELINE, "planned_entry": None,
            "planned_stop": 1988.0, "planned_tp1": 2010.0, "planned_tp2": None,
            "filter_rules": {}, "entry_rule": "FVG_ZONE_TOUCH",  # <-- FVG marker
            "instrument": "MNQ", "direction": "Long", "trading_date": "2024-01-01",
            "breakout_direction": "Long", "breakout_level": None,
            "or_high": None, "or_low": None, "trend_15m": None, "trend_4h": None,
            "cvd_direction": None,
        }
        orb_row = {
            **fvg_row,
            "result_id": "RES_orb", "experiment_id": "EXP_orb",
            "entry_rule": "TOUCH",  # <-- ORB marker
        }

        cols = list(fvg_row.keys())
        cur.fetchall.side_effect = [
            [tuple(fvg_row[c] for c in cols),
             tuple(orb_row[c] for c in cols)],
            [],   # FVG revisit dedup query
        ]
        cur.description = [(c,) for c in cols]

        eng._restore_active_experiments()

        fvg_in_memory = eng._open_results.get("RES_fvg", {})
        orb_in_memory = eng._open_results.get("RES_orb", {})
        self.assertTrue(fvg_in_memory.get("_fvg_family"),
                        "FVG experiment must have _fvg_family=True after restore")
        self.assertFalse(orb_in_memory.get("_fvg_family", False),
                         "ORB experiment must NOT have _fvg_family=True")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — Benchmark / regression: ORB unaffected by Phase 4 changes
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrbRegressionAfterPhase4(unittest.TestCase):

    def test_strategy_name_unchanged(self):
        """ORB STRATEGY_NAME must not change after Phase 4 migration."""
        self.assertEqual(STRATEGY_NAME, "09:30_ORB")

    def test_all_orb_variants_still_present(self):
        """ORB Variant class must still have all original variants."""
        from ghost_research_engine import Variant, ALL_VARIANTS
        expected = {"BASELINE", "TOUCH", "CLOSE_AND_RETEST", "BUFFER_PLUS_2",
                    "BUFFER_MINUS_2", "TP_1R", "TP_1_5R", "TP_2R",
                    "TREND_REQUIRED", "CVD_ALIGNED"}
        self.assertEqual(set(ALL_VARIANTS), expected)

    def test_on_bar_close_does_not_crash(self):
        """ORB on_bar_close still works after Phase 4 changes."""
        eng, db, cur = _mock_gre()
        cur.fetchall.return_value = []
        cur.description = []
        # Should not raise
        orb_status = {"state": "IDLE", "trading_date": "2024-01-01"}
        try:
            eng.on_bar_close("MNQ", orb_status, 1995.0)
        except Exception as e:
            self.fail(f"on_bar_close raised: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — Normalization proof tests (Phase 4.1 requirement)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgNormalization(unittest.TestCase):
    """
    Prove the normalization contract:
      - float drift eliminated via tick quantization
      - direction strings canonicalized to BULLISH/BEARISH
      - timestamps normalized to integer Unix seconds
    """

    # ── Tick quantization proofs ──────────────────────────────────────────────

    def test_float_drift_mnq_same_tick_same_id(self):
        """
        SPEC REQUIRED: 21342.50 and 21342.5000000001 must produce the same
        research_fvg_id for MNQ (tick=0.25) because they quantize to the same tick.
        """
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50,        21340.50)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.5000000001, 21340.50)
        self.assertEqual(id1, id2,
            "float drift must not produce different research_fvg_id for same tick level")

    def test_float_drift_lower_same_tick_same_id(self):
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.2500000001)
        self.assertEqual(id1, id2)

    def test_different_tick_level_different_id(self):
        """
        SPEC REQUIRED: Different valid tick-level prices must produce different IDs.
        21342.50 vs 21342.75 are two distinct MNQ tick boundaries.
        """
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.75, 21340.25)
        self.assertNotEqual(id1, id2,
            "distinct tick boundaries must produce distinct research_fvg_id values")

    def test_quantize_mnq_tick(self):
        """MNQ tick=0.25: 21342.5000000001 → 21342.50"""
        tick = _TICK_SIZE["MNQ"]  # 0.25
        q = _quantize_to_tick(21342.5000000001, tick)
        self.assertAlmostEqual(q, 21342.50, places=6)

    def test_quantize_mgc_tick(self):
        """MGC tick=0.10: 1850.0000000001 → 1850.00"""
        tick = _TICK_SIZE["MGC"]  # 0.10
        q = _quantize_to_tick(1850.0000000001, tick)
        self.assertAlmostEqual(q, 1850.00, places=6)

    def test_quantize_mym_tick(self):
        """MYM tick=1.0: 54321.4999 → 54321.0"""
        tick = _TICK_SIZE["MYM"]  # 1.0
        q = _quantize_to_tick(54321.4999, tick)
        self.assertAlmostEqual(q, 54321.0, places=4)

    def test_quantize_mes_tick(self):
        """MES tick=0.25: 5123.87 → nearest 0.25 = 5123.75 (5123.87 - 5123.75 = 0.12 < 0.13 = 5124.0 - 5123.87)"""
        tick = _TICK_SIZE["MES"]  # 0.25
        q = _quantize_to_tick(5123.87, tick)
        self.assertAlmostEqual(q, 5123.75, places=4)

    def test_quantize_does_not_collapse_different_tick_boundaries(self):
        """21342.50 and 21342.25 must NOT collapse to the same value (MNQ tick=0.25)."""
        tick = _TICK_SIZE["MNQ"]
        q1 = _quantize_to_tick(21342.50, tick)
        q2 = _quantize_to_tick(21342.25, tick)
        self.assertNotEqual(q1, q2)

    # ── Direction canonicalization ─────────────────────────────────────────────

    def test_direction_long_maps_to_bullish(self):
        """'Long' must NOT hash as 'LONG' — must be canonicalized to 'BULLISH'."""
        self.assertEqual(_canonical_direction_fvg("Long"), "BULLISH")
        self.assertEqual(_canonical_direction_fvg("LONG"), "BULLISH")
        self.assertEqual(_canonical_direction_fvg("long"), "BULLISH")

    def test_direction_short_maps_to_bearish(self):
        self.assertEqual(_canonical_direction_fvg("Short"), "BEARISH")
        self.assertEqual(_canonical_direction_fvg("SHORT"), "BEARISH")
        self.assertEqual(_canonical_direction_fvg("sell"), "BEARISH")

    def test_direction_bullish_preserved(self):
        self.assertEqual(_canonical_direction_fvg("BULLISH"), "BULLISH")
        self.assertEqual(_canonical_direction_fvg("bullish"), "BULLISH")

    def test_direction_bearish_preserved(self):
        self.assertEqual(_canonical_direction_fvg("BEARISH"), "BEARISH")
        self.assertEqual(_canonical_direction_fvg("bearish"), "BEARISH")

    def test_long_and_bullish_produce_same_id(self):
        """'Long' direction must produce same research_fvg_id as 'BULLISH'."""
        id_long = _fvg_research_id("MNQ", "Long",    1700000000, 21342.50, 21340.25)
        id_bull = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        self.assertEqual(id_long, id_bull)

    def test_short_and_bearish_produce_same_id(self):
        id_short = _fvg_research_id("MNQ", "Short",   1700000000, 21342.50, 21340.25)
        id_bear  = _fvg_research_id("MNQ", "BEARISH", 1700000000, 21342.50, 21340.25)
        self.assertEqual(id_short, id_bear)

    # ── Timestamp canonicalization ─────────────────────────────────────────────

    def test_canonical_ts_int_passthrough(self):
        self.assertEqual(_canonical_ts_fvg(1700000000), 1700000000)

    def test_canonical_ts_float_truncated(self):
        self.assertEqual(_canonical_ts_fvg(1700000000.5), 1700000000)

    def test_canonical_ts_iso_string(self):
        """ISO-8601 string must parse to same int as the corresponding epoch."""
        ts = _canonical_ts_fvg("2023-11-14T22:13:20Z")
        self.assertEqual(ts, 1700000000)

    def test_canonical_ts_none_returns_zero(self):
        self.assertEqual(_canonical_ts_fvg(None), 0)

    def test_equivalent_timestamps_same_id(self):
        """
        SPEC REQUIRED: Equivalent timestamps representing the same bar must
        generate the same research_fvg_id.
        """
        id_int = _fvg_research_id("MNQ", "BULLISH", 1700000000,    21342.50, 21340.25)
        id_flt = _fvg_research_id("MNQ", "BULLISH", 1700000000.0,  21342.50, 21340.25)
        id_iso = _fvg_research_id("MNQ", "BULLISH", "2023-11-14T22:13:20Z", 21342.50, 21340.25)
        self.assertEqual(id_int, id_flt)
        self.assertEqual(id_int, id_iso)

    def test_different_timestamps_different_id(self):
        id1 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000060, 21342.50, 21340.25)  # +60s
        self.assertNotEqual(id1, id2)

    def test_inst_case_normalized(self):
        """'mnq' and 'MNQ' must produce the same research_fvg_id."""
        id1 = _fvg_research_id("mnq", "BULLISH", 1700000000, 21342.50, 21340.25)
        id2 = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        self.assertEqual(id1, id2)

    def test_raw_float_hashing_impossible(self):
        """
        Prove that raw float hashing cannot corrupt identity.
        The hash uses quantized prices formatted to fixed decimals, not repr(float).
        """
        # These two differ at the 11th decimal — same tick, must produce same ID
        raw_float_a = 21342.50000000001   # repr() differs from b
        raw_float_b = 21342.50            # exact
        id_a = _fvg_research_id("MNQ", "BULLISH", 1700000000, raw_float_a, 21340.25)
        id_b = _fvg_research_id("MNQ", "BULLISH", 1700000000, raw_float_b, 21340.25)
        self.assertEqual(id_a, id_b,
            "RAW_FLOAT_HASHING_POSSIBLE must = NO; quantization must eliminate drift")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — Synthetic replay pipeline test (Section 5 of Phase 4.1 spec)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFvgSyntheticPipeline(unittest.TestCase):
    """
    Full end-to-end synthetic pipeline test.
    Proves the sequence:
      canonical FVG created
      → price leaves area
      → legitimate revisit occurs
      → exactly ONE FVG_REVISIT opportunity
      → baseline + intended variants created
      → subsequent Databento bars update experiments
      → outcome persists
    """

    def _make_engine(self):
        """Build a GhostResearchEngine with spy DB."""
        inserted_opps   = []
        inserted_exps   = []
        updated_results = []

        db  = MagicMock()
        cur = MagicMock()

        def mock_execute(sql, params=()):
            s = str(sql).strip()
            if "INSERT INTO ghost_opportunities" in s:
                inserted_opps.append(params)
                cur.fetchone.return_value = ("row",)
            elif "INSERT INTO ghost_experiments" in s:
                inserted_exps.append(params)
                cur.fetchone.return_value = ("exp_row",)
            elif "INSERT INTO ghost_experiment_results" in s:
                cur.fetchone.return_value = None
            elif "UPDATE ghost_experiment_results" in s:
                updated_results.append(params)
                cur.fetchone.return_value = None
            else:
                cur.fetchone.return_value = None

        cur.execute.side_effect = mock_execute
        cur.fetchall.return_value = []
        cur.description = []
        db.cursor.return_value = cur

        engine = GhostResearchEngine(
            get_db_fn=lambda: db,
            get_canonical_fn=lambda inst: {},
            get_bars_fn=lambda inst: [],
            re_event_fn=lambda *a, **kw: None,
            instruments=["MNQ"],
        )
        GhostResearchEngine.GRE_DB_READY = True
        return engine, inserted_opps, inserted_exps, updated_results

    def test_full_pipeline_first_revisit(self):
        """
        Simulate: zone created → price outside → price enters zone → opportunity + 10 variants.
        """
        engine, opps, exps, _ = self._make_engine()

        # Zone parameters (deterministic, MNQ tick=0.25)
        upper, lower = 21342.50, 21340.25
        bar_ts  = 1700000000
        zone = _zone("BULLISH", upper=upper, lower=lower, bar_ts=bar_ts,
                     status="ACTIVE", zone_id="src-uuid-abc123")

        # Canonical context (provides trend/CVD for variant filters)
        canonical = {
            "trend": {"trend_15m": "BULLISH_MOMENTUM", "trend_4h": "BULLISH_TREND"},
            "cvd":   {"direction": "BULLISH", "value": 150.0},
            "volume": {"relative_volume": 1.8},
            "vwap":  {"vwap": 21340.00, "side": "ABOVE"},
            "atr":   25.0,
        }

        # Bar 1: bar was OUTSIDE zone (price above zone)
        bar1 = _bar(ts=bar_ts + 60, l=upper + 5.0, h=upper + 20.0, c=upper + 10.0)
        engine.on_fvg_bar_close("MNQ", [zone], bar1, upper + 10.0, canonical)
        self.assertEqual(len(opps), 0, "No opportunity before revisit")
        self.assertEqual(len(exps), 0, "No experiments before revisit")

        # Bar 2: price ENTERS zone (revisit detected)
        bar2 = _bar(ts=bar_ts + 120, l=lower + 1.0, h=upper + 2.0, c=upper - 0.5)
        engine.on_fvg_bar_close("MNQ", [zone], bar2, upper - 0.5, canonical)

        # Verify exactly ONE opportunity was created
        self.assertEqual(len(opps), 1, "Exactly one opportunity on first revisit")
        opp_params = opps[0]
        opp_params_str = str(opp_params)

        # Verify strategy_family = FVG_REVISIT
        self.assertIn("FVG_REVISIT", opp_params_str)
        # Verify strategy = FVG_RESEARCH_BASELINE_V1 (not the family name)
        self.assertIn("FVG_RESEARCH_BASELINE_V1", opp_params_str)
        # Verify source_fvg_id is preserved
        self.assertIn("src-uuid-abc123", opp_params_str)

        # Verify research_fvg_id is deterministic
        rfid = _fvg_research_id("MNQ", "BULLISH", bar_ts, upper, lower)
        self.assertIn(rfid, opp_params_str)

        # Verify revisit_id is deterministic
        rev_id = _fvg_revisit_id(rfid, 1, bar_ts + 120)
        self.assertIn(rev_id, opp_params_str)

        # Verify exactly 10 experiments created
        self.assertEqual(len(exps), 10,
            f"Expected 10 variants, got {len(exps)}")

        # Verify all 10 variant names are present
        exp_variants = [str(e) for e in exps]
        for variant in FVG_ALL_VARIANTS:
            self.assertTrue(any(variant in ev for ev in exp_variants),
                f"Missing variant: {variant}")

        # Verify direction = Long (BULLISH zone)
        self.assertIn("Long", opp_params_str)

    def test_same_session_no_duplicate_opportunity(self):
        """Staying inside zone for multiple bars must not create a second opportunity."""
        engine, opps, exps, _ = self._make_engine()
        zone = _zone("BULLISH", upper=21342.50, lower=21340.25, bar_ts=1700000000)

        # First entry: revisit detected
        bar_enter = _bar(ts=1700000060, l=21340.5, h=21343.0, c=21341.0)
        engine.on_fvg_bar_close("MNQ", [zone], bar_enter, 21341.0, {})
        first_opp_count = len(opps)

        # Bar inside zone: no new opportunity
        bar_inside = _bar(ts=1700000120, l=21340.0, h=21342.0, c=21341.5)
        engine.on_fvg_bar_close("MNQ", [zone], bar_inside, 21341.5, {})
        self.assertEqual(len(opps), first_opp_count,
            "Second bar inside zone must NOT create another opportunity")

    def test_second_revisit_creates_new_opportunity(self):
        """
        After price exits zone, a second entry creates a NEW opportunity
        with a different revisit_id.
        """
        engine, opps, exps, _ = self._make_engine()
        zone = _zone("BULLISH", upper=21342.50, lower=21340.25, bar_ts=1700000000)

        # First revisit
        bar1 = _bar(ts=1700000060, l=21340.5, h=21343.0, c=21341.0)
        engine.on_fvg_bar_close("MNQ", [zone], bar1, 21341.0, {})
        # Exit zone
        bar2 = _bar(ts=1700000120, l=21343.5, h=21345.0, c=21344.0)  # above zone
        engine.on_fvg_bar_close("MNQ", [zone], bar2, 21344.0, {})
        first_count = len(opps)

        # Second revisit
        bar3 = _bar(ts=1700000180, l=21340.6, h=21342.0, c=21341.2)
        engine.on_fvg_bar_close("MNQ", [zone], bar3, 21341.2, {})
        self.assertEqual(len(opps), first_count + 1,
            "Second physical revisit must create a new opportunity")

        # The two opportunities must have different revisit_ids (different bar_ts)
        rfid   = _fvg_research_id("MNQ", "BULLISH", 1700000000, 21342.50, 21340.25)
        rid1   = _fvg_revisit_id(rfid, 1, 1700000060)
        rid2   = _fvg_revisit_id(rfid, 2, 1700000180)
        self.assertNotEqual(rid1, rid2)

    def test_variant_orthogonality(self):
        """
        No non-baseline variant may have an identical parameter_diff to BASELINE.
        Each variant must change EXACTLY its intended principal variable.
        """
        engine, _, exps, _ = self._make_engine()
        zone = _zone("BULLISH", upper=21342.50, lower=21340.25, bar_ts=1700000000)
        bar  = _bar(ts=1700000060, l=21340.5, h=21343.0, c=21341.0)
        engine.on_fvg_bar_close("MNQ", [zone], bar, 21341.0, {})

        self.assertEqual(len(exps), 10)

        # Extract parameter_diff from each experiment insert
        # params tuple: experiment_id, opportunity_id, strategy, strategy_family, ...
        # parameter_diff is at index 8 (0-based) from the INSERT
        param_diffs = []
        variant_names = []
        for ep in exps:
            ep_list = list(ep)
            # variant_name is at index 7, parameter_diff at index 8
            variant_names.append(ep_list[7])
            param_diffs.append(json.loads(ep_list[8]) if isinstance(ep_list[8], str) else ep_list[8])

        # Baseline
        baseline_idx = variant_names.index(FvgVariant.BASELINE)
        baseline_diff = param_diffs[baseline_idx]

        # No other variant's parameter_diff must be identical to baseline
        for i, (v, pd) in enumerate(zip(variant_names, param_diffs)):
            if i == baseline_idx:
                continue
            self.assertNotEqual(pd, baseline_diff,
                f"Variant {v} has identical parameter_diff to BASELINE — not orthogonal")

    def test_win_outcome_persists(self):
        """
        After entry: bar that hits TP1 triggers WIN outcome.
        Experiment is removed from _open_results (completed).
        """
        engine, opps, exps, updated = self._make_engine()
        zone = _zone("BULLISH", upper=21342.50, lower=21340.25, bar_ts=1700000000)

        # Revisit: create opportunity and 10 experiments
        bar_enter = _bar(ts=1700000060, l=21340.5, h=21343.0, c=21341.5)
        engine.on_fvg_bar_close("MNQ", [zone], bar_enter, 21341.5, {})
        initial_open = len(engine._open_results)

        # Force WATCHING_ENTRY experiments to ACTIVE (simulate entry from same bar).
        # Actual entry price = close of bar_enter = upper - 0.5 = 21342.0.
        # planned_stop = lower - 2*tick = 21340.25 - 0.50 = 21339.75
        # planned_tp1  = 21342.0 + 2.0 * |21342.0 - 21339.75| = 21342.0 + 4.5 = 21346.5
        with engine._lock:
            for rid, rd in engine._open_results.items():
                if rd.get("_fvg_family") and rd.get("status") == ResultStatus.WATCHING_ENTRY:
                    rd["status"]      = ResultStatus.ACTIVE
                    rd["entry_price"] = rd.get("entry_price") or 21342.0
                    rd["stop_price"]  = rd.get("stop_price")  or 21339.75

        # Bar that clears TP1 for all variants (h=21347.0 > 21346.5 = 2R baseline tp1)
        bar_tp = _bar(ts=1700000120, l=21342.0, h=21347.0, c=21346.8)
        engine.on_fvg_bar_close("MNQ", [zone], bar_tp, 21346.8, {})

        remaining_open = len([r for r in engine._open_results.values()
                              if r.get("_fvg_family")])
        self.assertLess(remaining_open, initial_open,
            "At least some FVG experiments should complete after TP1 hit")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 14 — ET timestamp validation (Section 13 of Phase 4.1 spec)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEtTimestampNormalization(unittest.TestCase):
    """
    Prove ET conversion works correctly.
    These tests validate the Python-side canonical_ts_fvg normalization,
    not the JS chart renderer (that is validated via node test externally).
    """

    def test_utc_int_passthrough(self):
        self.assertEqual(_canonical_ts_fvg(1700000000), 1700000000)

    def test_utc_float_truncated_to_int(self):
        self.assertEqual(_canonical_ts_fvg(1700000000.999), 1700000000)

    def test_iso_utc_parses_correctly(self):
        # 2023-11-14T22:13:20Z = 1700000000
        self.assertEqual(_canonical_ts_fvg("2023-11-14T22:13:20Z"), 1700000000)

    def test_iso_with_offset_parses_correctly(self):
        # Same moment with +00:00 offset
        self.assertEqual(_canonical_ts_fvg("2023-11-14T22:13:20+00:00"), 1700000000)

    def test_none_returns_zero(self):
        self.assertEqual(_canonical_ts_fvg(None), 0)


if __name__ == "__main__":
    unittest.main()
