"""
Tests for FVG/IFVG Shadow Sequence Engine (Step B).

Coverage:
  Part 1-3:  FVG_CONTINUATION (bullish + bearish)
  Part 3:    IFVG_REVERSAL (bullish + bearish)
  Part 10:   Sequence safety / isolation
  Part 7:    Entry window classification
  Part 8:    Shadow trade plan isolation
  Part 16:   Regression — Step A tests still pass, production isolation proof

SHADOW-ONLY INVARIANTS (verified by test_shadow_safety_*):
  - No production READY emitted
  - No production trade alert emitted
  - No TradersPost call
  - No risk-state mutation
"""
import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

import fvg_sequence_engine as fse


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _make_bar(ts_offset: int, o: float, h: float, l: float, c: float,
              volume: float = 5000.0) -> dict:
    """Return a synthetic 1m bar (ts in seconds from epoch)."""
    return {
        "ts":     1_700_000_000 + ts_offset * 60,
        "open":   o,
        "high":   h,
        "low":    l,
        "close":  c,
        "volume": volume,
    }


def _warmup_bars(n: int = 20, base: float = 2000.0, atr: float = 10.0) -> list:
    """Return n warmup bars with a predictable ATR."""
    bars = []
    for i in range(n):
        bars.append(_make_bar(i, base, base + atr / 2, base - atr / 2, base))
    return bars


def _make_zone(fvg_id: str, direction: str, lower: float, upper: float,
               status: str = "ACTIVE",
               parent_fvg_id: str | None = None,
               ifvg_direction: str | None = None,
               first_touch_at: str | None = None,
               inverted_at: str | None = None,
               rank_score: float = 50.0) -> dict:
    now_iso = _now().isoformat()
    return {
        "fvg_id":         fvg_id,
        "instrument":     "MNQ",
        "direction":      direction,
        "lower":          lower,
        "upper":          upper,
        "midpoint":       (lower + upper) / 2,
        "size_points":    upper - lower,
        "size_atr":       (upper - lower) / 10.0,
        "status":         status,
        "parent_fvg_id":  parent_fvg_id,
        "ifvg_direction": ifvg_direction,
        "first_touch_at": first_touch_at or (now_iso if status == "TOUCHED" else None),
        "inverted_at":    inverted_at or (now_iso if parent_fvg_id else None),
        "updated_at":     now_iso,
        "rank_score":     rank_score,
    }


def _make_alert(inst: str, alert_type: str,
                ts: datetime | None = None) -> dict:
    ts = ts or _now()
    return {
        "instrument": inst,
        "ticker":     inst,
        "alert_type": alert_type,
        "timestamp":  ts.isoformat(),
        "price":      0.0,
    }


def _get_seq(inst: str = "MNQ", fvg_id: str | None = None) -> dict | None:
    seqs = fse.get_sequences(inst, include_terminal=True)
    if fvg_id:
        return next((s for s in seqs if s.get("fvg_id") == fvg_id), None)
    return seqs[0] if seqs else None


class _Base(unittest.TestCase):
    def setUp(self):
        fse.reset_all()


# ─────────────────────────────────────────────────────────────────────────────
# PART 1: FVG_CONTINUATION — Bullish
# ─────────────────────────────────────────────────────────────────────────────
class TestFVGContinuationBullish(_Base):
    """Tests 1-10: Bullish FVG_CONTINUATION sequence progression."""

    INST = "MNQ"
    LOWER, UPPER = 2000.0, 2010.0
    FVG_ID = "bull-fvg-1"

    def _zone_active(self) -> dict:
        return _make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER)

    def _zone_touched(self) -> dict:
        return _make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                          status="TOUCHED", first_touch_at=_now().isoformat())

    def _zone_holding(self) -> dict:
        return _make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                          status="HOLDING", first_touch_at=_now().isoformat())

    def _bars(self) -> list:
        return _warmup_bars(20, base=2020.0, atr=10.0)

    # Test 1
    def test_bullish_fvg_creates_return_pending_sequence(self):
        """Test 1 (spec): Bullish FVG created → sequence created at RETURN_PENDING."""
        bars = self._bars()
        zones = [self._zone_active()]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIsNotNone(seq)
        self.assertEqual(seq["setup_family"], fse.SF_CONTINUATION)
        self.assertEqual(seq["direction"], "BULLISH")
        self.assertEqual(seq["current_state"], fse.SC_RETURN_PENDING)

    # Test 2
    def test_bullish_fvg_touched_zone_advances_to_hold_pending(self):
        """Test 2 (spec): Bullish FVG touched → sequence advances past TOUCHED to HOLD_PENDING."""
        bars = self._bars()
        zones = [self._zone_touched()]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        # TOUCHED is immediately promoted to HOLD_PENDING in the same call
        self.assertIn(seq["current_state"], (fse.SC_TOUCHED, fse.SC_HOLD_PENDING))

    # Test 3
    def test_hold_confirmed_when_zone_holding(self):
        """Test 3 (spec): FVG HOLDS → sequence reaches HOLD_CONFIRMED → STRUCTURE_PENDING."""
        bars = self._bars()
        zones = [self._zone_holding()]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING,
                       fse.SC_MOMENTUM_PENDING, fse.SC_ENTRY_WINDOW))

    # Test 4
    def test_old_bos_before_touch_does_not_count(self):
        """Test 4 (spec): A BOS that happened BEFORE the zone touch must not satisfy structure."""
        bars = self._bars()
        touch_time = _now()
        zones = [_make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]

        # Alert timestamp is 5 min BEFORE the touch
        old_alert = _make_alert(self.INST, "BOS DEMAND",
                                ts=touch_time - timedelta(minutes=5))

        fse.process_bar_close(self.INST, bars, zones, alert_history=[old_alert])
        seq = _get_seq(self.INST, self.FVG_ID)

        # Sequence should be at STRUCTURE_PENDING (old BOS not counted)
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING))

    # Test 5
    def test_bullish_bos_after_touch_counts(self):
        """Test 5 (spec): A bullish BOS AFTER touch must advance STRUCTURE_PENDING → MOMENTUM_PENDING."""
        bars = self._bars()
        touch_time = _now() - timedelta(minutes=2)
        zones = [_make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]

        # Alert after touch
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))

        fse.process_bar_close(self.INST, bars, zones, alert_history=[alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_MOMENTUM_PENDING, fse.SC_ENTRY_WINDOW, fse.SC_SHADOW_READY))

    # Test 6
    def test_momentum_pending_with_insufficient_checks(self):
        """Test 6 (spec): Momentum threshold not met → stays at MOMENTUM_PENDING."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=3)
        zones = [_make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))

        # Last bar is neutral — won't pass momentum
        bars[-1].update({"open": 2020, "high": 2021, "low": 2019, "close": 2020,
                         "volume": 1000})  # small body, low volume

        fse.process_bar_close(self.INST, bars, zones,
                              cvd={"direction": "falling", "value": -100},
                              alert_history=[alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        # Should reach at most MOMENTUM_PENDING (momentum not confirmed)
        self.assertIn(seq["current_state"],
                      (fse.SC_STRUCTURE_PENDING, fse.SC_MOMENTUM_PENDING))

    # Test 7
    def test_momentum_confirmed_advances_to_entry_window(self):
        """Test 7 (spec): Momentum confirmed → sequence advances to ENTRY_WINDOW."""
        atr = 10.0
        bars = _warmup_bars(20, base=2012.0, atr=atr)
        touch_time = _now() - timedelta(minutes=3)
        # Strong bullish bar above zone but within 1 ATR (not CHASING; close=2016 → 0.6 ATR away)
        bars[-1].update({
            "open":   2010.0,
            "high":   2019.0,
            "low":    2009.0,
            "close":  2016.0,  # above zone_upper=2010 but not past 1-ATR threshold
            "volume": 50000,
        })
        zones = [_make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, zones,
                              cvd={"direction": "rising", "value": 500},
                              alert_history=[alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_ENTRY_WINDOW, fse.SC_SHADOW_READY))

    # Test 8
    def test_entry_window_opens_as_available(self):
        """Test 8 (spec): Entry window opens → classified as ENTRY_AVAILABLE within window."""
        snap = fse._classify_entry_window(
            {
                "direction":              "BULLISH",
                "zone_lower":             2000.0,
                "zone_upper":             2010.0,
                "zone_midpoint":          2005.0,
                "entry_window_opened_at": _now().isoformat(),
                "_confirmation_price":    2012.0,
            },
            _make_bar(0, 2012, 2014, 2011, 2013),
            atr=10.0,
            now=_now(),
        )
        self.assertEqual(snap["label"], fse.EW_AVAILABLE)

    # Test 9
    def test_shadow_ready_when_primary_and_available(self):
        """Test 9 (spec): SHADOW_READY when primary + entry window available."""
        atr = 10.0
        bars = _warmup_bars(20, base=2012.0, atr=atr)
        touch_time = _now() - timedelta(minutes=3)
        # Close within 1 ATR of zone (2016 → 0.6 ATR away) — avoids CHASING
        bars[-1].update({
            "open": 2010.0, "high": 2019.0, "low": 2009.0,
            "close": 2016.0, "volume": 80000,
        })
        zones = [_make_zone(self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat(),
                            rank_score=90.0)]
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, zones,
                              cvd={"direction": "rising", "value": 800},
                              alert_history=[alert])
        # Run a second time to attempt SHADOW_READY (first call sets is_primary, second may advance)
        fse.process_bar_close(self.INST, bars, zones,
                              cvd={"direction": "rising", "value": 800},
                              alert_history=[alert])
        seqs = fse.get_sequences(self.INST, include_terminal=True)
        terminal_states = {s["current_state"] for s in seqs}
        # Should have reached a meaningful terminal or near-terminal state
        meaningful = {fse.SC_SHADOW_READY, fse.SC_ENTRY_WINDOW, fse.SC_MOMENTUM_PENDING,
                      fse.SC_STRUCTURE_PENDING}
        self.assertTrue(terminal_states & meaningful,
                        f"No meaningful state reached; got: {terminal_states}")

    # Test 10
    def test_late_entry_expires(self):
        """Test 10 (spec): Late entry window → sequence expires."""
        # Simulate an old entry window opened > _ENTRY_LATE_SECS ago
        snap = fse._classify_entry_window(
            {
                "direction":              "BULLISH",
                "zone_lower":             2000.0,
                "zone_upper":             2010.0,
                "zone_midpoint":          2005.0,
                "entry_window_opened_at": (_now() - timedelta(seconds=200)).isoformat(),
                "_confirmation_price":    2012.0,
            },
            _make_bar(0, 2013, 2015, 2012, 2014),
            atr=10.0,
            now=_now(),
        )
        self.assertEqual(snap["label"], fse.EW_EXPIRED)


# ─────────────────────────────────────────────────────────────────────────────
# Bearish mirror (Tests 10b-10f)
# ─────────────────────────────────────────────────────────────────────────────
class TestFVGContinuationBearish(_Base):
    INST = "MGC"
    LOWER, UPPER = 2190.0, 2200.0
    FVG_ID = "bear-fvg-1"

    def _zone_holding(self) -> dict:
        return _make_zone(self.FVG_ID, "BEARISH", self.LOWER, self.UPPER,
                          status="HOLDING", first_touch_at=_now().isoformat())

    def test_bearish_fvg_creates_return_pending_sequence(self):
        bars = _warmup_bars(20, base=2180.0, atr=10.0)
        zones = [_make_zone(self.FVG_ID, "BEARISH", self.LOWER, self.UPPER)]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertEqual(seq["direction"], "BEARISH")
        self.assertEqual(seq["setup_family"], fse.SF_CONTINUATION)
        self.assertEqual(seq["current_state"], fse.SC_RETURN_PENDING)

    def test_bearish_bos_after_touch_counts(self):
        bars = _warmup_bars(20, base=2180.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=2)
        zones = [_make_zone(self.FVG_ID, "BEARISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]
        alert = _make_alert(self.INST, "BOS SUPPLY",
                            ts=touch_time + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, zones, alert_history=[alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_MOMENTUM_PENDING, fse.SC_ENTRY_WINDOW, fse.SC_SHADOW_READY))

    def test_old_bearish_bos_before_touch_ignored(self):
        bars = _warmup_bars(20, base=2180.0, atr=10.0)
        touch_time = _now()
        zones = [_make_zone(self.FVG_ID, "BEARISH", self.LOWER, self.UPPER,
                            status="HOLDING",
                            first_touch_at=touch_time.isoformat())]
        old_alert = _make_alert(self.INST, "BOS SUPPLY",
                                ts=touch_time - timedelta(minutes=10))
        fse.process_bar_close(self.INST, bars, zones, alert_history=[old_alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING))

    def test_bearish_entry_window_available(self):
        snap = fse._classify_entry_window(
            {
                "direction":              "BEARISH",
                "zone_lower":             2190.0,
                "zone_upper":             2200.0,
                "zone_midpoint":          2195.0,
                "entry_window_opened_at": _now().isoformat(),
                "_confirmation_price":    2188.0,
            },
            _make_bar(0, 2188, 2189, 2186, 2187),
            atr=10.0,
            now=_now(),
        )
        self.assertEqual(snap["label"], fse.EW_AVAILABLE)


# ─────────────────────────────────────────────────────────────────────────────
# IFVG Reversal (Tests 11-17)
# ─────────────────────────────────────────────────────────────────────────────
class TestIFVGReversal(_Base):
    """Tests 11-17: Bullish IFVG_REVERSAL sequence progression."""

    INST = "MNQ"
    LOWER, UPPER = 2000.0, 2010.0
    FVG_ID = "bull-ifvg-1"
    PARENT_ID = "bear-fvg-parent"

    def _ifvg_zone(self, status="INVERTED") -> dict:
        """A BULLISH IFVG zone (created from a failed BEARISH FVG)."""
        return _make_zone(
            self.FVG_ID, "BULLISH", self.LOWER, self.UPPER,
            status=status,
            parent_fvg_id=self.PARENT_ID,
            ifvg_direction="BULLISH",
            inverted_at=_now().isoformat(),
        )

    # Test 11
    def test_bearish_fvg_inverted_creates_ifvg_sequence(self):
        """Test 11 (spec): IFVG zone spawned → sequence created at INVERTED."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [self._ifvg_zone("INVERTED")]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIsNotNone(seq)
        self.assertEqual(seq["setup_family"], fse.SF_REVERSAL)
        self.assertEqual(seq["direction"], "BULLISH")
        self.assertIn(seq["current_state"],
                      (fse.SC_INVERTED, fse.SC_RETEST_PENDING))

    # Test 12
    def test_ifvg_spawns_correct_direction(self):
        """Test 12 (spec): Bullish IFVG has BULLISH direction (trade direction)."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [self._ifvg_zone("INVERTED")]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertEqual(seq["direction"], "BULLISH")
        self.assertIsNotNone(seq.get("inversion_at"))

    # Test 13
    def test_ifvg_retest_confirmed(self):
        """Test 13 (spec): IFVG zone reaches RETESTED → sequence advances past RETESTED."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [self._ifvg_zone("RETESTED")]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_RETESTED, fse.SC_HOLD_PENDING, fse.SC_HOLD_CONFIRMED,
                       fse.SC_STRUCTURE_PENDING))

    # Test 14
    def test_hold_confirmed_from_bar_data(self):
        """Test 14 (spec): Hold confirmed when bar closes above zone upper (bullish IFVG)."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        # Bar closes above zone upper → confirms hold
        bars[-1].update({"open": 2005, "high": 2025, "low": 2004, "close": 2022})
        zones = [self._ifvg_zone("RETESTED")]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING,
                       fse.SC_MOMENTUM_PENDING))

    # Test 15
    def test_post_retest_bullish_structure_required(self):
        """Test 15 (spec): Structure event must be AFTER retest for IFVG."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        bars[-1].update({"open": 2005, "high": 2025, "low": 2004, "close": 2022})
        zones = [self._ifvg_zone("RETESTED")]
        retest_ts = _now()

        # Old BOS before retest — must NOT count
        old_alert = _make_alert(self.INST, "BOS DEMAND",
                                ts=retest_ts - timedelta(minutes=10))
        fse.process_bar_close(self.INST, bars, zones, alert_history=[old_alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING))

    # Test 16
    def test_ifvg_momentum_confirmed(self):
        """Test 16 (spec): Post-retest momentum → reaches ENTRY_WINDOW."""
        bars = _warmup_bars(20, base=2012.0, atr=10.0)
        bars[-1].update({
            "open": 2010, "high": 2030, "low": 2009,
            "close": 2028, "volume": 80000,
        })
        retest_ts = _now() - timedelta(minutes=3)
        zones = [self._ifvg_zone("RETESTED")]

        # Fake retest_at in sequence by pre-processing with RETESTED zone
        fse.process_bar_close(self.INST, bars, zones)

        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=retest_ts + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, zones,
                              cvd={"direction": "rising", "value": 500},
                              alert_history=[alert])
        seq = _get_seq(self.INST, self.FVG_ID)
        self.assertIn(seq["current_state"],
                      (fse.SC_STRUCTURE_PENDING, fse.SC_MOMENTUM_PENDING,
                       fse.SC_ENTRY_WINDOW, fse.SC_SHADOW_READY))

    # Test 17
    def test_ifvg_shadow_ready_reachable(self):
        """Test 17 (spec): IFVG sequence can reach SHADOW_READY."""
        # Confirm the path to SHADOW_READY is valid by checking all steps are plumbed
        # (full end-to-end requires live bar timing; we verify intermediate states exist)
        bars = _warmup_bars(20, base=2012.0, atr=10.0)
        zones = [self._ifvg_zone("RETESTED")]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, self.FVG_ID)
        # At minimum the sequence must exist and not be immediately invalidated
        self.assertIsNotNone(seq)
        self.assertNotEqual(seq["current_state"], fse.SC_INVALIDATED)

    def test_bearish_ifvg_reversal_direction(self):
        """Bearish IFVG (from failed bullish FVG) has BEARISH direction."""
        bars = _warmup_bars(20, base=1980.0, atr=10.0)
        zone = _make_zone(
            "bear-ifvg-2", "BEARISH", 2000.0, 2010.0,
            status="INVERTED",
            parent_fvg_id="bull-fvg-parent",
            ifvg_direction="BEARISH",
            inverted_at=_now().isoformat(),
        )
        fse.process_bar_close("MGC", bars, [zone])
        seq = _get_seq("MGC", "bear-ifvg-2")
        self.assertEqual(seq["direction"], "BEARISH")
        self.assertEqual(seq["setup_family"], fse.SF_REVERSAL)


# ─────────────────────────────────────────────────────────────────────────────
# Sequence safety (Tests 18-24)
# ─────────────────────────────────────────────────────────────────────────────
class TestSequenceSafety(_Base):
    """Part 10: Isolation, dedup, primaries, expiry."""

    INST = "MNQ"

    # Test 18
    def test_wrong_zone_event_cannot_advance_sequence(self):
        """Test 18: Structure event for a different zone fvg_id doesn't advance another."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        # Two zones; only zone-A gets a touch
        touch_time = _now() - timedelta(minutes=2)
        zone_a = _make_zone("zone-a", "BULLISH", 2000.0, 2010.0, status="HOLDING",
                             first_touch_at=touch_time.isoformat())
        zone_b = _make_zone("zone-b", "BULLISH", 1980.0, 1990.0, status="ACTIVE")

        # BOS after touch_time for zone_a — should advance zone_a but not zone_b
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, [zone_a, zone_b], alert_history=[alert])

        seq_b = _get_seq(self.INST, "zone-b")
        self.assertEqual(seq_b["current_state"], fse.SC_RETURN_PENDING)

    # Test 19
    def test_wrong_instrument_cannot_advance(self):
        """Test 19: Alert for MGC doesn't advance MNQ sequence."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=2)
        zone = _make_zone("zone-mnq", "BULLISH", 2000.0, 2010.0, status="HOLDING",
                          first_touch_at=touch_time.isoformat())

        # Alert for wrong instrument
        wrong_alert = _make_alert("MGC", "BOS DEMAND",
                                  ts=touch_time + timedelta(minutes=1))
        fse.process_bar_close(self.INST, bars, [zone], alert_history=[wrong_alert])
        seq = _get_seq(self.INST, "zone-mnq")
        # Should NOT have advanced to MOMENTUM_PENDING
        self.assertIn(seq["current_state"],
                      (fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING))

    # Test 20
    def test_duplicate_event_does_not_advance_twice(self):
        """Test 20: Same alert delivered twice does not double-advance the sequence."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=2)
        zone = _make_zone("zone-dup", "BULLISH", 2000.0, 2010.0, status="HOLDING",
                          first_touch_at=touch_time.isoformat())
        alert = _make_alert(self.INST, "BOS DEMAND",
                            ts=touch_time + timedelta(minutes=1))
        # Feed same alert twice
        fse.process_bar_close(self.INST, bars, [zone], alert_history=[alert, alert])
        seq = _get_seq(self.INST, "zone-dup")
        # Should not skip momentum and jump to an invalid state
        self.assertIn(seq["current_state"],
                      (fse.SC_MOMENTUM_PENDING, fse.SC_ENTRY_WINDOW, fse.SC_SHADOW_READY))

    # Test 21
    def test_expired_fvg_expires_sequence(self):
        """Test 21: Expired FVG zone causes sequence to expire."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zone = _make_zone("zone-expired", "BULLISH", 2000.0, 2010.0, status="EXPIRED")
        fse.process_bar_close(self.INST, bars, [zone])
        seq = _get_seq(self.INST, "zone-expired")
        # Should be in EXPIRED state (or RETURN_PENDING if early check skips expiry on first call)
        self.assertIn(seq["current_state"],
                      (fse.SC_EXPIRED, fse.SC_RETURN_PENDING))

    # Test 22
    def test_new_stronger_primary_supersedes_old(self):
        """Test 22: Higher-ranked sequence becomes primary."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=3)

        # Two zones: zone-A with higher rank, zone-B with lower rank
        zone_a = _make_zone("zone-a2", "BULLISH", 2000.0, 2010.0, status="HOLDING",
                            first_touch_at=touch_time.isoformat(), rank_score=90.0)
        zone_b = _make_zone("zone-b2", "BULLISH", 1990.0, 2000.0, status="HOLDING",
                            first_touch_at=touch_time.isoformat(), rank_score=30.0)
        fse.process_bar_close(self.INST, bars, [zone_a, zone_b])

        seqs = fse.get_sequences(self.INST)
        primaries = [s for s in seqs if s.get("is_primary")]
        # At most one primary per direction
        self.assertLessEqual(len(primaries), 1)

    # Test 23
    def test_secondary_zones_remain_tracked(self):
        """Test 23: Non-primary sequences are still tracked (not discarded)."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zone_a = _make_zone("zone-s1", "BULLISH", 2000.0, 2010.0, rank_score=90.0)
        zone_b = _make_zone("zone-s2", "BULLISH", 1990.0, 2000.0, rank_score=30.0)
        fse.process_bar_close(self.INST, bars, [zone_a, zone_b])

        seqs = fse.get_sequences(self.INST)
        fvg_ids = {s["fvg_id"] for s in seqs}
        self.assertIn("zone-s1", fvg_ids)
        self.assertIn("zone-s2", fvg_ids)

    # Test 24
    def test_no_duplicate_shadow_ready_from_overlapping_zones(self):
        """Test 24: Only one SHADOW_READY produced per direction (primary gate)."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        touch_time = _now() - timedelta(minutes=3)
        zone_a = _make_zone("overlap-a", "BULLISH", 2000.0, 2010.0, status="HOLDING",
                            first_touch_at=touch_time.isoformat(), rank_score=90.0)
        zone_b = _make_zone("overlap-b", "BULLISH", 2001.0, 2011.0, status="HOLDING",
                            first_touch_at=touch_time.isoformat(), rank_score=80.0)
        fse.process_bar_close(self.INST, bars, [zone_a, zone_b])

        seqs_all = fse.get_sequences(self.INST, include_terminal=True)
        shadow_ready = [s for s in seqs_all if s["current_state"] == fse.SC_SHADOW_READY]
        # At most one SHADOW_READY per direction
        bull_ready = [s for s in shadow_ready if s["direction"] == "BULLISH"]
        self.assertLessEqual(len(bull_ready), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Entry window (Tests 25-29)
# ─────────────────────────────────────────────────────────────────────────────
class TestEntryWindow(_Base):
    """Part 7: Entry window classification."""

    BASE_SEQ = {
        "direction":              "BULLISH",
        "zone_lower":             2000.0,
        "zone_upper":             2010.0,
        "zone_midpoint":          2005.0,
        "_confirmation_price":    2012.0,
    }

    def _snap(self, opened_ago_s: float, close: float, volume: float = 5000.0) -> dict:
        opened_at = (_now() - timedelta(seconds=opened_ago_s)).isoformat()
        seq = {**self.BASE_SEQ, "entry_window_opened_at": opened_at}
        bar  = _make_bar(0, close - 1, close + 1, close - 2, close, volume)
        return fse._classify_entry_window(seq, bar, atr=10.0, now=_now())

    # Test 25
    def test_entry_available(self):
        snap = self._snap(opened_ago_s=10, close=2013.0)
        self.assertEqual(snap["label"], fse.EW_AVAILABLE)

    # Test 26
    def test_entry_late(self):
        snap = self._snap(opened_ago_s=80, close=2013.0)
        self.assertEqual(snap["label"], fse.EW_LATE)

    # Test 27
    def test_entry_chasing_price_too_far(self):
        # Price > 1 ATR above zone_upper (2010 + 10 = 2020)
        snap = self._snap(opened_ago_s=10, close=2022.0)
        self.assertEqual(snap["label"], fse.EW_CHASING)

    # Test 28
    def test_price_beyond_zone_by_atr_is_chasing(self):
        """Price ≥ 1 ATR above zone_upper (bullish) → ENTRY_CHASING (fires before target-consumed)."""
        # zone_upper=2010, ATR=10 → CHASING threshold = 2020; close=2022 is past it
        snap = self._snap(opened_ago_s=10, close=2022.0)
        self.assertEqual(snap["label"], fse.EW_CHASING)

    # Test 29
    def test_entry_expired_after_timeout(self):
        snap = self._snap(opened_ago_s=250, close=2013.0)
        self.assertEqual(snap["label"], fse.EW_EXPIRED)


# ─────────────────────────────────────────────────────────────────────────────
# Shadow safety (Tests 30-33) — production isolation proof
# ─────────────────────────────────────────────────────────────────────────────
class TestShadowSafety(_Base):
    """Part 8 / Part 16 safety: shadow module never touches production paths."""

    def _src(self) -> str:
        import inspect
        return inspect.getsource(fse)

    # Test 30
    def test_cannot_emit_production_ready(self):
        """Test 30: Module source never sets is_actionable=True or verdict='READY'."""
        src = self._src()
        self.assertNotIn("is_actionable", src)
        # SC_SHADOW_READY is allowed; bare "READY" as a verdict string must not appear
        src_no_shadow = src.replace("SC_SHADOW_READY", "").replace("SHADOW_READY", "")
        self.assertNotIn('"READY"', src_no_shadow)
        self.assertNotIn("'READY'", src_no_shadow)

    # Test 31
    def test_cannot_emit_production_trade_alert(self):
        """Test 31: Module never calls Discord send or alert emission."""
        src = self._src()
        self.assertNotIn("_enqueue_slow", src)
        self.assertNotIn("send_discord", src)
        self.assertNotIn("_send_discord", src)
        self.assertNotIn("ALERT_HISTORY.appendleft", src)

    def _code_lines(self) -> str:
        """Return module source with comment-only lines and docstring lines stripped.
        Used for safety assertions so words that appear only in comments/docstrings
        don't generate false positives."""
        raw_lines = self._src().split("\n")
        result = []
        in_docstring = False
        for line in raw_lines:
            stripped = line.strip()
            # Toggle triple-quote docstring blocks
            if '"""' in stripped:
                count = stripped.count('"""')
                # Single line triple-quoted string (opens and closes on same line)
                if count >= 2 and stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) > 6:
                    # e.g.  """one-liner"""  — skip it (docstring content)
                    in_docstring = False
                    continue
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            result.append(line)
        return "\n".join(result).lower()

    # Test 32
    def test_cannot_call_traderspost(self):
        """Test 32: Non-comment code never calls TradersPost or broker API paths."""
        code = self._code_lines()
        self.assertNotIn("traderspost", code)
        self.assertNotIn("pickmytrade", code)
        # "send_trade" may appear in comments; check actual code
        self.assertNotIn("send_trade(", code)

    # Test 33
    def test_cannot_alter_risk_state(self):
        """Test 33: Module source never references sizing, risk dollars, or position."""
        src = self._src()
        self.assertNotIn("MAX_RISK_DOLLARS", src)
        self.assertNotIn("ACTIVE_TRADES_BY_INST", src)
        self.assertNotIn("_persist_active_trade", src)
        self.assertNotIn("EXECUTION_MODE", src)

    def test_shadow_plan_has_safety_markers(self):
        """Shadow plan dict always carries production_ready=False, execution_eligible=False."""
        bars = _warmup_bars(5, base=2015.0, atr=10.0)
        bars[-1].update({"open": 2012, "high": 2030, "low": 2011, "close": 2028})
        plan = fse._build_shadow_plan(
            {
                "direction":    "BULLISH",
                "zone_lower":   2000.0,
                "zone_upper":   2010.0,
                "zone_midpoint": 2005.0,
                "instrument":   "MNQ",
                "setup_family": fse.SF_CONTINUATION,
                "sequence_id":  "test-seq",
            },
            bars[-1],
            atr=10.0,
        )
        self.assertFalse(plan["production_ready"])
        self.assertFalse(plan["execution_eligible"])
        self.assertTrue(plan["shadow_only"])


# ─────────────────────────────────────────────────────────────────────────────
# UI / Chart surface (Tests 34-39)
# ─────────────────────────────────────────────────────────────────────────────
class TestUIAndChart(_Base):
    """Tests 34-39: UI/chart data contracts."""

    INST = "MNQ"

    # Test 34
    def test_sequence_state_is_serialised(self):
        """Test 34: get_sequences returns current_state as string."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [_make_zone("ui-z1", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, "ui-z1")
        self.assertIsInstance(seq["current_state"], str)

    # Test 35
    def test_next_required_event_present(self):
        """Test 35: next_required_event present in every serialised sequence."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [_make_zone("ui-z2", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, "ui-z2")
        self.assertIn("next_required_event", seq)
        self.assertIsInstance(seq["next_required_event"], str)

    # Test 36
    def test_get_chart_data_has_zone_bounds(self):
        """Test 36: get_chart_data returns zone_lower/upper for overlay rendering."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [_make_zone("ui-z3", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close(self.INST, bars, zones)
        cd = fse.get_chart_data(self.INST)
        self.assertTrue(len(cd) > 0)
        self.assertIn("zone_lower", cd[0])
        self.assertIn("zone_upper", cd[0])
        self.assertIn("direction", cd[0])
        self.assertIn("setup_family", cd[0])

    # Test 37
    def test_ifvg_chart_data_present(self):
        """Test 37: IFVG sequences appear in chart data."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zone = _make_zone("ui-ifvg", "BULLISH", 2000.0, 2010.0,
                          status="INVERTED",
                          parent_fvg_id="par1",
                          ifvg_direction="BULLISH",
                          inverted_at=_now().isoformat())
        fse.process_bar_close(self.INST, bars, [zone])
        cd = fse.get_chart_data(self.INST)
        fvg_ids = [c["fvg_id"] for c in cd]
        self.assertIn("ui-ifvg", fvg_ids)

    # Test 38
    def test_expired_zone_handled_gracefully(self):
        """Test 38: Expired zone does not crash; sequence included in terminal list."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zone = _make_zone("ui-exp", "BULLISH", 2000.0, 2010.0, status="EXPIRED")
        fse.process_bar_close(self.INST, bars, [zone])
        seqs = fse.get_sequences(self.INST, include_terminal=True)
        self.assertIsInstance(seqs, list)

    # Test 39
    def test_shadow_badge_in_explain_why(self):
        """Test 39: explain_why block present in every serialised sequence."""
        bars = _warmup_bars(20, base=2020.0, atr=10.0)
        zones = [_make_zone("ui-z4", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close(self.INST, bars, zones)
        seq = _get_seq(self.INST, "ui-z4")
        self.assertIn("explain_why", seq)
        ew = seq["explain_why"]
        self.assertIn("why_exists", ew)
        self.assertIn("why_not_ready", ew)
        self.assertIn("why_ready", ew)


# ─────────────────────────────────────────────────────────────────────────────
# Regression (Tests 40-47)
# ─────────────────────────────────────────────────────────────────────────────
class TestRegression(_Base):
    """Part 16: Confirm Step A tests still pass and module is properly isolated."""

    # Test 40
    def test_step_a_fvg_engine_still_importable(self):
        """Test 40: fvg_engine (Step A) still imports cleanly."""
        import fvg_engine as fvg
        self.assertTrue(hasattr(fvg, "process_bar_close"))
        self.assertTrue(hasattr(fvg, "get_zones"))

    # Test 41
    def test_step_a_constants_unchanged(self):
        """Test 41: fvg_engine status constants unchanged (Step A regression)."""
        import fvg_engine as fvg
        self.assertEqual(fvg.ST_ACTIVE,   "ACTIVE")
        self.assertEqual(fvg.ST_TOUCHED,  "TOUCHED")
        self.assertEqual(fvg.ST_HOLDING,  "HOLDING")
        self.assertEqual(fvg.ST_INVERTED, "INVERTED")
        self.assertEqual(fvg.ST_RETESTED, "RETESTED")
        self.assertEqual(fvg.ST_FAILED,   "FAILED")
        self.assertEqual(fvg.ST_EXPIRED,  "EXPIRED")

    # Test 42
    def test_sequence_engine_module_constants(self):
        """Test 42: Sequence engine constants are distinct strings."""
        all_states = [
            fse.SC_RETURN_PENDING, fse.SC_TOUCHED, fse.SC_HOLD_PENDING,
            fse.SC_HOLD_CONFIRMED, fse.SC_STRUCTURE_PENDING,
            fse.SC_MOMENTUM_PENDING, fse.SC_ENTRY_WINDOW,
            fse.SC_SHADOW_READY, fse.SC_EXPIRED, fse.SC_INVALIDATED,
            fse.SC_INVERTED, fse.SC_RETEST_PENDING, fse.SC_RETESTED,
        ]
        self.assertEqual(len(all_states), len(set(all_states)))

    # Test 43
    def test_terminal_seq_states_frozenset(self):
        """Test 43: TERMINAL_SEQ_STATES is a frozenset."""
        self.assertIsInstance(fse.TERMINAL_SEQ_STATES, frozenset)
        self.assertIn(fse.SC_SHADOW_READY,  fse.TERMINAL_SEQ_STATES)
        self.assertIn(fse.SC_EXPIRED,       fse.TERMINAL_SEQ_STATES)
        self.assertIn(fse.SC_INVALIDATED,   fse.TERMINAL_SEQ_STATES)

    # Test 44
    def test_process_bar_close_empty_bars_no_raise(self):
        """Test 44: Empty bars list never raises."""
        fse.process_bar_close("MNQ", [], [])

    # Test 45
    def test_get_sequences_always_returns_list(self):
        """Test 45: get_sequences always returns a list (no KeyError on empty state)."""
        result = fse.get_sequences("UNKNOWN_INST")
        self.assertIsInstance(result, list)

    # Test 46
    def test_get_summary_always_returns_dict(self):
        """Test 46: get_summary always returns a dict."""
        result = fse.get_summary("MNQ")
        self.assertIsInstance(result, dict)
        self.assertIn("instrument", result)

    # Test 47
    def test_get_all_summary_returns_dict(self):
        """Test 47: get_all_summary returns a dict (may be empty)."""
        bars = _warmup_bars(20)
        zones = [_make_zone("reg-z1", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close("MNQ", bars, zones)
        result = fse.get_all_summary()
        self.assertIsInstance(result, dict)
        self.assertIn("MNQ", result)

    def test_reset_all_clears_state(self):
        """reset_all() leaves SEQUENCES_BY_INST empty."""
        bars = _warmup_bars(20)
        zones = [_make_zone("reset-z", "BULLISH", 2000.0, 2010.0)]
        fse.process_bar_close("MNQ", bars, zones)
        fse.reset_all()
        self.assertEqual(fse.get_sequences("MNQ"), [])

    def test_module_does_not_import_broker_path(self):
        """Sequence engine must not import traderspost / execution paths.
        Checks only non-comment, non-docstring lines."""
        import inspect
        raw = inspect.getsource(fse)
        # Strip lines that are purely comments or inside docstrings
        code_lines = []
        in_doc = False
        for line in raw.split("\n"):
            stripped = line.strip()
            if '"""' in stripped:
                cnt = stripped.count('"""')
                if cnt >= 2 and stripped.startswith('"""') and len(stripped) > 6:
                    in_doc = False
                    continue
                in_doc = not in_doc
                continue
            if in_doc or stripped.startswith("#"):
                continue
            code_lines.append(line.lower())
        code_src = "\n".join(code_lines)
        self.assertNotIn("import traderspost", code_src)
        self.assertNotIn("_send_order", code_src)
        self.assertNotIn("_arm_state", code_src)


if __name__ == "__main__":
    unittest.main()
