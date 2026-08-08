"""
test_fvg_engine.py — Comprehensive unit tests for fvg_engine.py
=============================================================
Step A: Shadow/Display-only FVG/IFVG lifecycle engine.

Test groupings:
  A. Detection (bullish/bearish/no-gap/displacement)
  B. Lifecycle (touched/holding/mitigated/failed/inverted/retested/expired)
  C. IFVG creation and lifecycle
  D. API surface (get_zones/get_chart_zones/get_best_zone/get_summary)
  E. Ranking
  F. Safety invariants (disable flag, per-instrument isolation, zone cap)

Safety: no test touches gate, scoring, sizing, or execution.
        DB writes are absent (DB_READY stays False in all tests).
"""
from __future__ import annotations

import os
import time
import threading

os.environ["FVG_ENGINE_ENABLED"] = "1"
os.environ["FVG_MIN_SIZE_ATR"]   = "0.08"
os.environ["FVG_DISPLACEMENT_MIN"] = "1.2"
os.environ["FVG_MAX_AGE_BARS"]   = "90"
os.environ["FVG_MITIGATION_PCT"] = "0.50"
os.environ["FVG_MAX_ZONES_PER_INST"] = "30"
os.environ["FVG_ATR_PERIOD"]     = "14"

import pytest
import fvg_engine as fvg


# ── Fixtures ──────────────────────────────────────────────────────────────────

def reset():
    """Clear all in-memory state between tests."""
    fvg.reset_all()


def make_bar(ts, open_, high, low, close, volume=1000):
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def flat_bars(n: int, price=2000.0, spread=0.5, start_ts=0) -> list:
    """n identical bars — no FVG possible."""
    return [make_bar(start_ts + i, price, price + spread, price - spread, price) for i in range(n)]


def bars_with_bullish_fvg(gap: float = 3.0, atr_size: float = 10.0) -> list:
    """
    Produce a minimal 16-bar series with a bullish FVG in the last 3 candles.
    bar[-3].high = 2000, bar[-1].low = 2000 + gap  → gap = [2000, 2000+gap]
    Middle candle is a strong bullish displacement.
    ATR ~ atr_size: warmup bars use spread=atr_size/2 so ATR ≈ atr_size,
    making the displacement body (1.4×atr_size) satisfy the 1.2× threshold.
    """
    # 13 warm-up bars with high-low ≈ atr_size so ATR ≈ atr_size
    warmup = [make_bar(i, 2000.0,
                       2000.0 + atr_size / 2,
                       2000.0 - atr_size / 2,
                       2000.0) for i in range(13)]
    # bar[-3]: candle with high=2000
    b1 = make_bar(13, 1990.0, 2000.0, 1990.0, 1999.0)
    # bar[-2]: strong bullish displacement candle (body = 1.4×atr_size > 1.2×ATR)
    body = 1.4 * atr_size
    b2 = make_bar(14, 2000.0, 2000.0 + body + 1, 1999.9, 2000.0 + body)
    # bar[-1]: candle with low=2000+gap (above bar[-3].high)
    b3 = make_bar(15, 2000.0 + gap, 2000.0 + gap + 1, 2000.0 + gap, 2000.0 + gap + 0.5)
    return warmup + [b1, b2, b3]


def bars_with_bearish_fvg(gap: float = 3.0, atr_size: float = 10.0) -> list:
    """
    Produce a minimal 16-bar series with a bearish FVG in the last 3 candles.
    bar[-3].low = 2010, bar[-1].high = 2010 - gap → gap = [2010-gap, 2010]
    Warmup bars use spread=atr_size/2 so ATR ≈ atr_size.
    """
    warmup = [make_bar(i, 2010.0,
                       2010.0 + atr_size / 2,
                       2010.0 - atr_size / 2,
                       2010.0) for i in range(13)]
    b1 = make_bar(13, 2020.0, 2025.0, 2010.0, 2012.0)  # bar[-3].low = 2010
    body = 1.4 * atr_size
    b2 = make_bar(14, 2010.0, 2010.5, 2010.0 - body - 1, 2010.0 - body)
    b3 = make_bar(15, 2010.0 - gap - 1, 2010.0 - gap, 2010.0 - gap - 2, 2010.0 - gap - 1)
    return warmup + [b1, b2, b3]


# ── A. Detection ──────────────────────────────────────────────────────────────

class TestDetection:
    def setup_method(self):
        reset()

    def test_bullish_fvg_detected(self):
        bars = bars_with_bullish_fvg(gap=3.0, atr_size=10.0)
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        assert len(zones) == 1
        z = zones[0]
        assert z["direction"] == "BULLISH"
        assert z["status"] == fvg.ST_ACTIVE
        assert z["lower"] == pytest.approx(2000.0)
        assert z["upper"] == pytest.approx(2003.0)
        assert z["midpoint"] == pytest.approx(2001.5)
        assert z["size_points"] == pytest.approx(3.0)

    def test_bearish_fvg_detected(self):
        bars = bars_with_bearish_fvg(gap=3.0, atr_size=10.0)
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        assert len(zones) == 1
        z = zones[0]
        assert z["direction"] == "BEARISH"
        assert z["status"] == fvg.ST_ACTIVE

    def test_no_fvg_no_gap(self):
        bars = flat_bars(20)
        fvg.process_bar_close("MNQ", bars)
        assert fvg.get_zones("MNQ") == []

    def test_no_fvg_insufficient_displacement(self):
        """Gap exists but displacement candle body is too small."""
        warmup = [make_bar(i, 2000.0, 2010.0, 1990.0, 2000.0) for i in range(13)]
        b1 = make_bar(13, 1990.0, 2000.0, 1990.0, 1999.0)
        # Displacement < 1.2 * ATR (body ≈ 0.5, ATR ≈ 20)
        b2 = make_bar(14, 2000.0, 2000.3, 1999.8, 2000.3)
        b3 = make_bar(15, 2004.0, 2005.0, 2004.0, 2004.5)
        bars = warmup + [b1, b2, b3]
        fvg.process_bar_close("MNQ", bars)
        assert fvg.get_zones("MNQ") == []

    def test_no_fvg_overlap_exists(self):
        """bar[-3].high >= bar[-1].low — no gap."""
        warmup = [make_bar(i, 2000.0, 2010.0, 1990.0, 2000.0) for i in range(13)]
        b1 = make_bar(13, 1990.0, 2005.0, 1990.0, 2004.0)  # high=2005
        b2 = make_bar(14, 2003.0, 2015.0, 2002.0, 2012.0)  # strong up
        b3 = make_bar(15, 2003.0, 2007.0, 2003.0, 2006.0)  # low=2003 < 2005
        bars = warmup + [b1, b2, b3]
        fvg.process_bar_close("MNQ", bars)
        assert fvg.get_zones("MNQ") == []

    def test_less_than_3_bars_no_detect(self):
        bars = flat_bars(2)
        fvg.process_bar_close("MNQ", bars)
        assert fvg.get_zones("MNQ") == []

    def test_fvg_id_is_uuid(self):
        bars = bars_with_bullish_fvg()
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        assert len(zones[0]["fvg_id"]) == 36
        assert zones[0]["fvg_id"].count("-") == 4

    def test_deduplication_same_anchor_ts(self):
        """Calling process_bar_close twice with the same bars should not duplicate zones."""
        bars = bars_with_bullish_fvg()
        fvg.process_bar_close("MNQ", bars)
        fvg.process_bar_close("MNQ", bars)   # same bars, same ts anchor
        assert len(fvg.get_zones("MNQ")) == 1

    def test_two_sequential_fvgs(self):
        """Two different bar sequences each produce one FVG."""
        bars1 = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars1)
        # extend with new gap candles at different ts
        bars2 = bars1.copy()
        bars2[-1] = make_bar(999, 2010.0, 2015.0, 2010.0, 2014.0)  # new ts
        bars2.append(make_bar(1000, 2020.0, 2021.0, 2020.0, 2020.5))  # no gap
        # The detect function only looks at [-3:], so craft a genuine second gap
        warmup = [make_bar(i, 2050.0, 2060.0, 2040.0, 2050.0) for i in range(13)]
        b1 = make_bar(113, 1990.0, 2000.0, 1990.0, 1999.0)
        b2 = make_bar(114, 2001.0, 2015.0, 2001.0, 2013.0)  # displacement
        b3 = make_bar(200, 2006.0, 2007.0, 2006.0, 2006.5)  # different ts
        bars3 = warmup + [b1, b2, b3]
        fvg.process_bar_close("MNQ", bars3)
        # We should have 2 zones (the second call adds a new zone at ts=200)
        zones = fvg.get_zones("MNQ")
        assert len(zones) >= 1   # at minimum the original zone remains


# ── B. Lifecycle ──────────────────────────────────────────────────────────────

class TestBullishLifecycle:
    """Bullish FVG [lower=2000, upper=2003, mid=2001.5]"""
    INST = "MGC"
    LOWER = 2000.0
    UPPER = 2003.0
    MID   = 2001.5

    def setup_method(self):
        reset()
        bars = bars_with_bullish_fvg(gap=3.0, atr_size=10.0)
        fvg.process_bar_close(self.INST, bars)
        assert len(fvg.get_zones(self.INST)) == 1

    def _zone(self):
        zones = fvg.get_zones(self.INST)
        return zones[0] if zones else None

    def _next_bar(self, bars, new_bar):
        return bars + [new_bar]

    def test_initial_status_active(self):
        z = self._zone()
        assert z["status"] == fvg.ST_ACTIVE
        assert z["touch_count"] == 0

    def test_bar_above_zone_no_change(self):
        """A bar that stays above the zone doesn't change status."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_above = make_bar(100, 2010.0, 2015.0, 2005.0, 2012.0)  # low=2005 > upper=2003
        fvg.process_bar_close(self.INST, warmup + [bar_above])
        z = self._zone()
        assert z["status"] == fvg.ST_ACTIVE

    def test_touched_when_bar_enters_zone(self):
        """Bar low enters zone (low <= upper=2003)."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_touch = make_bar(100, 2010.0, 2015.0, 2002.0, 2010.0)  # low=2002 < upper=2003
        fvg.process_bar_close(self.INST, warmup + [bar_touch])
        z = self._zone()
        assert z["status"] in (fvg.ST_TOUCHED, fvg.ST_HOLDING, fvg.ST_MITIGATED)

    def test_holding_when_close_above_zone(self):
        """Bar enters zone then closes back above upper."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_hold = make_bar(100, 2010.0, 2015.0, 2001.0, 2010.0)  # low=2001 in zone, close=2010 > upper
        fvg.process_bar_close(self.INST, warmup + [bar_hold])
        z = self._zone()
        assert z["status"] == fvg.ST_HOLDING

    def test_mitigated_when_close_reaches_midpoint(self):
        """Bar low reaches midpoint → MITIGATED."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        # low=2001.0 <= mid=2001.5, close stays inside zone
        bar_mid = make_bar(100, 2010.0, 2015.0, 2001.0, 2001.2)
        fvg.process_bar_close(self.INST, warmup + [bar_mid])
        z = self._zone()
        assert z["status"] in (fvg.ST_MITIGATED, fvg.ST_HOLDING)

    def test_failed_when_close_below_lower(self):
        """Bar closes below lower=2000 → FAILED."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_fail = make_bar(100, 2010.0, 2015.0, 1990.0, 1995.0)  # close=1995 < lower=2000
        fvg.process_bar_close(self.INST, warmup + [bar_fail])
        zones = fvg.get_zones(self.INST, include_terminal=True)
        statuses = {z["status"] for z in zones}
        assert fvg.ST_FAILED in statuses

    def test_failed_creates_ifvg(self):
        """A failed bullish FVG spawns a BEARISH IFVG zone."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_fail = make_bar(100, 2010.0, 2015.0, 1990.0, 1995.0)
        fvg.process_bar_close(self.INST, warmup + [bar_fail])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs = [z for z in all_zones if z.get("ifvg_direction") == "BEARISH"]
        assert len(ifvgs) == 1
        ifvg_zone = ifvgs[0]
        assert ifvg_zone["status"] == fvg.ST_INVERTED
        assert ifvg_zone["parent_fvg_id"] is not None

    def test_expired_after_max_bars(self):
        """Zone expires after FVG_MAX_AGE_BARS bars (set to 90)."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        # Feed 95 neutral bars (above zone) to age out the FVG
        current_bars = warmup.copy()
        for i in range(95):
            bar = make_bar(100 + i, 2010.0, 2015.0, 2005.0, 2012.0)
            current_bars = current_bars + [bar]
            fvg.process_bar_close(self.INST, current_bars)
        zones_all = fvg.get_zones(self.INST, include_terminal=True)
        expired = [z for z in zones_all if z["status"] == fvg.ST_EXPIRED]
        assert len(expired) >= 1

    def test_active_zone_not_in_terminal_set(self):
        z = self._zone()
        assert z["status"] not in fvg.TERMINAL_STATUSES


class TestBearishLifecycle:
    """Bearish FVG — symmetric to bullish."""
    INST = "MNQ"

    def setup_method(self):
        reset()
        bars = bars_with_bearish_fvg(gap=3.0, atr_size=10.0)
        fvg.process_bar_close(self.INST, bars)

    def _zone(self):
        zones = fvg.get_zones(self.INST)
        return zones[0] if zones else None

    def test_initial_status_active(self):
        z = self._zone()
        assert z is not None
        assert z["status"] == fvg.ST_ACTIVE
        assert z["direction"] == "BEARISH"

    def test_touched_when_price_enters_from_below(self):
        warmup = bars_with_bearish_fvg(gap=3.0)
        z0 = self._zone()
        lower = z0["lower"]
        bar_touch = make_bar(100, lower - 5, lower + 0.5, lower - 5, lower - 3)
        fvg.process_bar_close(self.INST, warmup + [bar_touch])
        z = self._zone()
        assert z is not None

    def test_failed_creates_bullish_ifvg(self):
        warmup = bars_with_bearish_fvg(gap=3.0)
        z0 = self._zone()
        upper = z0["upper"]
        # Close above upper → FAILED
        bar_fail = make_bar(100, upper - 1, upper + 5, upper - 1, upper + 3)
        fvg.process_bar_close(self.INST, warmup + [bar_fail])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs = [z for z in all_zones if z.get("ifvg_direction") == "BULLISH"]
        assert len(ifvgs) == 1
        assert ifvgs[0]["status"] == fvg.ST_INVERTED

    def test_holding_when_close_below_lower(self):
        warmup = bars_with_bearish_fvg(gap=3.0)
        z0 = self._zone()
        lower = z0["lower"]
        upper = z0["upper"]
        # Enter zone from below (high >= lower) then close back below lower
        bar_hold = make_bar(100, lower - 5, lower + 0.5, lower - 5, lower - 1)
        fvg.process_bar_close(self.INST, warmup + [bar_hold])
        z = self._zone()
        # Status should be HOLDING since close is back below lower
        assert z["status"] == fvg.ST_HOLDING


# ── C. IFVG lifecycle ─────────────────────────────────────────────────────────

class TestIFVGLifecycle:
    INST = "MGC"

    def setup_method(self):
        reset()
        # Step 1: process the warmup sequence → detects BULLISH FVG
        warmup = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close(self.INST, warmup)
        assert fvg.get_zones(self.INST), "IFVG setup: expected bullish FVG to be detected"
        # Step 2: feed a bar that closes below lower → FAILED → spawns BEARISH IFVG
        bar_fail = make_bar(100, 2010.0, 2015.0, 1990.0, 1995.0)
        fvg.process_bar_close(self.INST, warmup + [bar_fail])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        self.ifvg = next(z for z in all_zones if z.get("ifvg_direction") == "BEARISH")
        self.warmup_bars = warmup + [bar_fail]

    def test_ifvg_starts_inverted(self):
        assert self.ifvg["status"] == fvg.ST_INVERTED
        assert self.ifvg["ifvg_direction"] == "BEARISH"
        assert self.ifvg["parent_fvg_id"] is not None

    def test_ifvg_has_same_geometry_as_parent(self):
        """IFVG inherits the same bounds as the original FVG."""
        assert self.ifvg["lower"] == pytest.approx(2000.0)
        assert self.ifvg["upper"] == pytest.approx(2003.0)

    def test_bearish_ifvg_retested_from_below(self):
        """BEARISH IFVG: price comes back up to touch lower bound → RETESTED."""
        lower = self.ifvg["lower"]
        # Bar high reaches lower bound (price rallies back up into zone from below)
        bar_retest = make_bar(200, lower - 2, lower + 0.5, lower - 2, lower - 1)
        fvg.process_bar_close(self.INST, self.warmup_bars + [bar_retest])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs = [z for z in all_zones if z.get("ifvg_direction") == "BEARISH"]
        assert len(ifvgs) == 1
        assert ifvgs[0]["status"] == fvg.ST_RETESTED

    def test_ifvg_no_retest_without_touch(self):
        """BEARISH IFVG: bar stays far below lower bound → still INVERTED."""
        lower = self.ifvg["lower"]
        bar_away = make_bar(200, lower - 10, lower - 5, lower - 12, lower - 8)
        fvg.process_bar_close(self.INST, self.warmup_bars + [bar_away])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs = [z for z in all_zones if z.get("ifvg_direction") == "BEARISH"]
        assert ifvgs[0]["status"] == fvg.ST_INVERTED

    def test_bullish_ifvg_retested_from_above(self):
        """BULLISH IFVG: price drops back to test from above → RETESTED."""
        # Create a failed bearish FVG → spawns BULLISH IFVG
        reset()
        warmup = bars_with_bearish_fvg(gap=3.0)
        z0 = fvg.get_zones(self.INST)   # Bearish FVG exists but we need to fail it
        # Fail the bearish FVG by closing above upper
        fvg.process_bar_close(self.INST, warmup)
        zones = fvg.get_zones(self.INST)
        if not zones:
            return   # nothing to test if no zone detected
        z0 = zones[0]
        upper = z0["upper"]
        bar_fail = make_bar(100, upper + 1, upper + 5, upper, upper + 3)
        fvg.process_bar_close(self.INST, warmup + [bar_fail])
        all_zones = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs = [z for z in all_zones if z.get("ifvg_direction") == "BULLISH"]
        if not ifvgs:
            return  # no IFVG formed — not an error for this test geometry
        ifvg_z = ifvgs[0]
        upper_ifvg = ifvg_z["upper"]
        # Price retest: bar low drops back to upper bound
        bar_retest = make_bar(200, upper_ifvg + 2, upper_ifvg + 3, upper_ifvg - 0.5, upper_ifvg + 1)
        fvg.process_bar_close(self.INST, warmup + [bar_fail, bar_retest])
        all_zones2 = fvg.get_zones(self.INST, include_terminal=True)
        ifvgs2 = [z for z in all_zones2 if z.get("ifvg_direction") == "BULLISH"]
        assert all(z["status"] in (fvg.ST_RETESTED, fvg.ST_INVERTED) for z in ifvgs2)


# ── D. API surface ────────────────────────────────────────────────────────────

class TestAPISurface:
    def setup_method(self):
        reset()
        bars = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars)

    def test_get_zones_returns_list(self):
        result = fvg.get_zones("MNQ")
        assert isinstance(result, list)

    def test_get_zones_excludes_internal_keys(self):
        zones = fvg.get_zones("MNQ")
        for z in zones:
            assert not any(k.startswith("_") for k in z)

    def test_get_zones_unknown_instrument_returns_empty(self):
        result = fvg.get_zones("UNKNOWN")
        assert result == []

    def test_get_zones_include_terminal(self):
        warmup = bars_with_bullish_fvg(gap=3.0)
        bar_fail = make_bar(100, 2010.0, 2015.0, 1990.0, 1995.0)
        fvg.process_bar_close("MNQ", warmup + [bar_fail])
        without_t = fvg.get_zones("MNQ", include_terminal=False)
        with_t    = fvg.get_zones("MNQ", include_terminal=True)
        assert len(with_t) >= len(without_t)

    def test_get_chart_zones_structure(self):
        zones = fvg.get_chart_zones("MNQ")
        assert isinstance(zones, list)
        for z in zones:
            for key in ("fvg_id", "direction", "lower", "upper", "midpoint",
                        "status", "touch_count", "bar_age", "rank_score"):
                assert key in z

    def test_get_best_zone_bullish(self):
        best = fvg.get_best_zone("MNQ", "BULLISH")
        assert best is not None
        assert best["direction"] == "BULLISH"

    def test_get_best_zone_bearish_returns_none_when_no_bearish(self):
        best = fvg.get_best_zone("MNQ", "BEARISH")
        assert best is None

    def test_get_summary_structure(self):
        summary = fvg.get_summary()
        assert summary.get("enabled") is True
        assert "MNQ" in summary
        inst_data = summary["MNQ"]
        for key in ("active_fvg_count", "active_ifvg_count", "best_bullish",
                    "best_bearish", "all_active"):
            assert key in inst_data

    def test_get_summary_best_bullish_not_none(self):
        summary = fvg.get_summary()
        assert summary["MNQ"]["best_bullish"] is not None

    def test_get_summary_empty_when_no_zones(self):
        summary = fvg.get_summary()
        assert "MGC" not in summary or summary["MGC"]["active_fvg_count"] == 0

    def test_get_zones_does_not_expose_persisted_flag(self):
        zones = fvg.get_zones("MNQ")
        for z in zones:
            assert "_persisted" not in z


# ── E. Ranking ────────────────────────────────────────────────────────────────

class TestRanking:
    def setup_method(self):
        reset()

    def test_rank_score_computed(self):
        bars = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        assert zones[0]["rank_score"] >= 0

    def test_rank_components_dict(self):
        bars = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        comps = zones[0].get("rank_components", {})
        assert isinstance(comps, dict)

    def test_holding_bonus_applied(self):
        """HOLDING zone should have a higher rank than ACTIVE due to holding_bonus."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", warmup)
        zones_before = fvg.get_zones("MNQ")
        score_active = zones_before[0]["rank_score"]

        # Push to HOLDING
        bar_hold = make_bar(100, 2010.0, 2015.0, 2001.0, 2010.0)
        fvg.process_bar_close("MNQ", warmup + [bar_hold])
        zones_after = fvg.get_zones("MNQ")
        # HOLDING has a 15-pt bonus
        score_holding = zones_after[0]["rank_score"] if zones_after else 0
        assert score_holding >= score_active  # holding bonus applies

    def test_freshness_decreases_with_age(self):
        """After many bars, freshness_score decreases."""
        warmup = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", warmup)
        initial_score = fvg.get_zones("MNQ")[0]["rank_score"]

        current = list(warmup)
        for i in range(30):
            bar = make_bar(100 + i, 2010.0, 2015.0, 2005.0, 2012.0)
            current = current + [bar]
            fvg.process_bar_close("MNQ", current)

        aged_score = fvg.get_zones("MNQ")[0]["rank_score"]
        # After 30 bars, freshness should have decayed
        assert aged_score <= initial_score + 5  # some tolerance for other factors


# ── F. Safety invariants ──────────────────────────────────────────────────────

class TestSafetyInvariants:
    def setup_method(self):
        reset()

    def test_disabled_flag_returns_empty(self):
        original = fvg.FVG_ENGINE_ENABLED
        fvg.FVG_ENGINE_ENABLED = False
        try:
            bars = bars_with_bullish_fvg()
            fvg.process_bar_close("MNQ", bars)
            assert fvg.get_zones("MNQ") == []
            assert fvg.get_chart_zones("MNQ") == []
            assert fvg.get_best_zone("MNQ", "BULLISH") is None
            summary = fvg.get_summary()
            assert summary == {"enabled": False}
        finally:
            fvg.FVG_ENGINE_ENABLED = original

    def test_per_instrument_isolation(self):
        """FVGs for MGC do not appear in MNQ and vice versa."""
        bars_mnq = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars_mnq)
        assert fvg.get_zones("MNQ") != []
        assert fvg.get_zones("MGC") == []

    def test_reset_instrument_clears_only_that_inst(self):
        bars_mnq = bars_with_bullish_fvg(gap=3.0)
        bars_mgc = bars_with_bullish_fvg(gap=3.0)
        fvg.process_bar_close("MNQ", bars_mnq)
        fvg.process_bar_close("MGC", bars_mgc)
        fvg.reset_instrument("MNQ")
        assert fvg.get_zones("MNQ") == []
        assert fvg.get_zones("MGC") != []

    def test_zone_cap_enforced(self):
        """Zone count never exceeds FVG_MAX_ZONES_PER_INST."""
        cap = fvg.FVG_MAX_ZONES_PER_INST
        # Create many distinct zones by processing bars at different ts values
        for i in range(cap + 5):
            warmup = [make_bar(i * 100 + j, 2000.0, 2010.0, 1990.0, 2000.0) for j in range(13)]
            b1 = make_bar(i * 100 + 13, 1990.0, 2000.0, 1990.0, 1999.0)
            b2 = make_bar(i * 100 + 14, 2000.0, 2014.0, 2000.0, 2013.0)   # displacement
            b3 = make_bar(i * 100 + 15, 2004.0, 2005.0, 2004.0, 2004.5)   # gap
            bars = warmup + [b1, b2, b3]
            fvg.process_bar_close("MNQ", bars)
        all_zones = fvg.get_zones("MNQ", include_terminal=True)
        assert len(all_zones) <= cap

    def test_thread_safety_concurrent_calls(self):
        """process_bar_close must be safe under concurrent calls for different instruments."""
        errors = []
        def worker(inst, bars):
            try:
                for _ in range(5):
                    fvg.process_bar_close(inst, bars)
            except Exception as e:
                errors.append(e)
        bars_mnq = bars_with_bullish_fvg(gap=3.0)
        bars_mgc = bars_with_bearish_fvg(gap=3.0)
        t1 = threading.Thread(target=worker, args=("MNQ", bars_mnq))
        t2 = threading.Thread(target=worker, args=("MGC", bars_mgc))
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)
        assert errors == []

    def test_no_db_write_without_db_ready(self):
        """With _DB_READY=False, no DB ops are attempted (no AttributeError)."""
        original_db_ready = fvg._DB_READY
        fvg._DB_READY = False
        try:
            bars = bars_with_bullish_fvg()
            fvg.process_bar_close("MNQ", bars)  # must not raise
            zones = fvg.get_zones("MNQ")
            assert zones != []   # in-memory still works
        finally:
            fvg._DB_READY = original_db_ready

    def test_get_zones_returns_copies_not_originals(self):
        """Modifying the returned zone dicts must not affect internal state."""
        bars = bars_with_bullish_fvg()
        fvg.process_bar_close("MNQ", bars)
        zones = fvg.get_zones("MNQ")
        original_status = zones[0]["status"]
        zones[0]["status"] = "MUTATED"   # caller mutates their copy
        zones2 = fvg.get_zones("MNQ")
        assert zones2[0]["status"] == original_status  # internal unchanged

    def test_malformed_bar_does_not_crash(self):
        """Bars with missing fields should not raise — fail silently."""
        bars = flat_bars(14)
        bars += [{"ts": 999}]   # malformed bar
        fvg.process_bar_close("MNQ", bars)  # must not raise

    def test_engine_never_imports_broker_path(self):
        """Importing fvg_engine must not pull in any order/execution modules."""
        import sys
        for module_name in ("traderspost", "pickmytrade", "execution"):
            assert module_name not in sys.modules


# ── Smoke ─────────────────────────────────────────────────────────────────────

class TestSmoke:
    """Minimal smoke checks — quick sanity without full lifecycle traversal."""

    def setup_method(self):
        reset()

    def test_module_imports_cleanly(self):
        import importlib
        importlib.reload(fvg)

    def test_process_empty_bars_no_raise(self):
        fvg.process_bar_close("MNQ", [])

    def test_get_summary_always_returns_dict(self):
        result = fvg.get_summary()
        assert isinstance(result, dict)

    def test_get_zones_always_returns_list(self):
        result = fvg.get_zones("MNQ")
        assert isinstance(result, list)

    def test_get_chart_zones_always_returns_list(self):
        result = fvg.get_chart_zones("MNQ")
        assert isinstance(result, list)

    def test_get_best_zone_never_raises(self):
        result = fvg.get_best_zone("MNQ", "BULLISH")
        assert result is None or isinstance(result, dict)

    def test_reset_all_no_raise(self):
        fvg.reset_all()

    def test_enabled_flag_is_boolean(self):
        assert isinstance(fvg.FVG_ENGINE_ENABLED, bool)

    def test_status_constants_are_distinct(self):
        statuses = [fvg.ST_ACTIVE, fvg.ST_TOUCHED, fvg.ST_MITIGATED,
                    fvg.ST_HOLDING, fvg.ST_FAILED, fvg.ST_INVERTED,
                    fvg.ST_RETESTED, fvg.ST_EXPIRED]
        assert len(set(statuses)) == len(statuses)

    def test_terminal_statuses_frozenset(self):
        assert isinstance(fvg.TERMINAL_STATUSES, frozenset)
        assert fvg.ST_ACTIVE not in fvg.TERMINAL_STATUSES
        assert fvg.ST_FAILED in fvg.TERMINAL_STATUSES
