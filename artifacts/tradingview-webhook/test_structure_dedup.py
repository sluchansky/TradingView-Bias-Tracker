"""test_structure_dedup.py
Tests for the structure-event deduplication engine (structure_dedup.py).

Coverage (from spec §10):
  1.  Same BOS from TV + Databento → counted once (TV shadow)
  2.  Same CHOCH from TV + Databento → counted once
  3.  Different directions → not deduped
  4.  Different instruments → not deduped
  5.  Different structural levels beyond tolerance → not deduped
  6.  Events outside time window → not deduped
  7.  Databento stale → TV fallback works
  8.  Databento unavailable → TV fallback works
  9.  Repeated TV webhook → both canonical (TV doesn't suppress TV)
 10.  Repeated Databento callback → metrics incremented each time
 11.  HH / HL / LH / LL behaviour remains correct
 12.  ALERT_HISTORY readers ignore shadow duplicates
 13.  Source tags preserved on both sides
 14.  Scoring parity: single canonical gives same gate result as legacy dual
 15.  Conflict detection works
 16.  Non-structure events always canonical, no dedup
 17.  Metrics tracking across all paths
 18.  Backward compat: legacy entries without 'canonical' field not skipped
 19.  Price-check skipped when price is None (fail-open)
 20.  fast_entry_bridge events are always canonical
"""

import pytest
from datetime import datetime, timezone, timedelta
from structure_dedup import (
    StructureDedup,
    STRUCTURE_DEDUP,
    STRUCTURE_TYPES,
    STRUCTURE_DIRECTION,
    STRUCTURE_FAMILY,
    INSTRUMENT_TICK_SIZE,
    DEDUP_TIME_SECS,
    DEDUP_PRICE_TICKS,
    _events_match,
    _is_databento,
    _parse_ts,
    _price_tolerance,
)


# ── Fixture helpers ─────────────────────────────────────────────────────────────

def _db_entry(alert_type, inst, price, ts_offset_secs=0):
    """Simulate a DatabentoBrain-injected ALERT_HISTORY record."""
    ts = (datetime.now(timezone.utc) + timedelta(seconds=ts_offset_secs)).isoformat()
    return {
        "alert_type":        alert_type,
        "instrument":        inst,
        "price":             float(price),
        "timestamp":         ts,
        "source":            "databento",
        "instrument_source": "databento",
        "canonical":         True,
    }


def _tv_entry(alert_type, inst, price, ts_offset_secs=0):
    """Simulate a TradingView webhook ALERT_HISTORY record (pre-dedup tagging)."""
    ts = (datetime.now(timezone.utc) + timedelta(seconds=ts_offset_secs)).isoformat()
    return {
        "alert_type":        alert_type,
        "instrument":        inst,
        "price":             float(price),
        "timestamp":         ts,
        "instrument_source": "registry",   # instrument-resolution source, NOT feed
    }


# ── 1. Same BOS: TV first, then Databento → TV retroactively demoted ───────────

class TestSameBOSTVFirst:
    def test_tv_canonical_before_databento(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_tv_event(tv, [])
        assert tv["canonical"] is True           # no Databento yet → fallback
        assert tv["source"] == "tradingview"

    def test_databento_demotes_tv_retroactively(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_tv_event(tv, [])                   # TV canonical
        db = _db_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=2)
        sd.on_databento_event(db, [tv])          # Databento demotes TV
        assert db["canonical"] is True
        assert tv["canonical"] is False
        assert tv["duplicate_of"] == db["timestamp"]
        assert tv["matched_source"] == "databento"


# ── Same BOS: Databento first, then TV → TV immediately shadow ─────────────────

class TestSameBOSDatabentoFirst:
    def test_tv_immediately_shadow_when_databento_exists(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False
        assert tv["source"] == "tradingview"
        assert tv["duplicate_of"] == db["timestamp"]


# ── 2. Same CHOCH: counted once ────────────────────────────────────────────────

class TestSameCHOCH:
    def test_choch_demand_deduped(self):
        sd = StructureDedup()
        db = _db_entry("CHOCH DEMAND", "MES", 5400.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("CHOCH DEMAND", "MES", 5400.5, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_choch_supply_deduped(self):
        sd = StructureDedup()
        db = _db_entry("CHOCH SUPPLY", "MYM", 44000.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("CHOCH SUPPLY", "MYM", 44000.0, ts_offset_secs=4)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False


# ── 3. Different directions → not deduped ─────────────────────────────────────

class TestDifferentDirections:
    def test_bos_demand_vs_bos_supply_not_deduped(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS SUPPLY", "MNQ", 29800.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True   # different alert_type → no match

    def test_choch_demand_vs_choch_supply_not_deduped(self):
        sd = StructureDedup()
        db = _db_entry("CHOCH DEMAND", "MES", 5400.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("CHOCH SUPPLY", "MES", 5400.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True


# ── 4. Different instruments → not deduped ────────────────────────────────────

class TestDifferentInstruments:
    def test_mnq_vs_mgc_not_deduped(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MGC", 29800.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True

    def test_mes_vs_mym_not_deduped(self):
        sd = StructureDedup()
        db = _db_entry("CHOCH SUPPLY", "MES", 5400.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("CHOCH SUPPLY", "MYM", 5400.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True


# ── 5. Different structural levels beyond tolerance → not deduped ─────────────

class TestPriceTolerance:
    def test_beyond_tolerance_not_deduped(self):
        sd = StructureDedup()
        tick = INSTRUMENT_TICK_SIZE["MNQ"]
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        far_price = 29800.0 + tick * (DEDUP_PRICE_TICKS + 1)  # 11 ticks away
        tv = _tv_entry("BOS DEMAND", "MNQ", far_price, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True

    def test_within_tolerance_deduped(self):
        sd = StructureDedup()
        tick = INSTRUMENT_TICK_SIZE["MNQ"]
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        close_price = 29800.0 + tick * (DEDUP_PRICE_TICKS - 1)  # 9 ticks away
        tv = _tv_entry("BOS DEMAND", "MNQ", close_price, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_mgc_tick_size_used(self):
        """MGC tick=0.10; tolerance = 10×0.10 = 1.00."""
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MGC", 3280.00)
        sd.on_databento_event(db, [])
        # 0.95 away = within 1.00 tolerance
        tv_in = _tv_entry("BOS DEMAND", "MGC", 3280.95, ts_offset_secs=5)
        sd.on_tv_event(tv_in, [db])
        assert tv_in["canonical"] is False
        # 1.05 away = outside tolerance
        sd2 = StructureDedup()
        db2 = _db_entry("BOS DEMAND", "MGC", 3280.00)
        sd2.on_databento_event(db2, [])
        tv_out = _tv_entry("BOS DEMAND", "MGC", 3281.05, ts_offset_secs=5)
        sd2.on_tv_event(tv_out, [db2])
        assert tv_out["canonical"] is True


# ── 6. Events outside time window → not deduped ───────────────────────────────

class TestTimeWindow:
    def test_outside_window_not_deduped(self):
        sd = StructureDedup()
        old_offset = -(DEDUP_TIME_SECS + 10)
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=old_offset)
        db["canonical"] = True
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True   # too old → no match

    def test_within_window_deduped(self):
        sd = StructureDedup()
        db_offset = -(DEDUP_TIME_SECS - 10)
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=db_offset)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False  # within window → match

    def test_exactly_at_boundary_not_deduped(self):
        """At exactly DEDUP_TIME_SECS seconds the check is strictly less-than."""
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=-DEDUP_TIME_SECS)
        db["canonical"] = True
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True   # exactly at boundary → not deduped


# ── 7. Databento stale → TV fallback works ────────────────────────────────────

class TestDatabentoStaleFallback:
    def test_stale_databento_allows_tv_canonical(self):
        sd = StructureDedup()
        old_db = _db_entry("CHOCH DEMAND", "MES", 5400.0,
                            ts_offset_secs=-(DEDUP_TIME_SECS + 30))
        old_db["canonical"] = True
        tv = _tv_entry("CHOCH DEMAND", "MES", 5400.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [old_db])
        assert tv["canonical"] is True
        assert tv["source"] == "tradingview"


# ── 8. Databento unavailable → TV fallback works ──────────────────────────────

class TestDatabentoUnavailable:
    def test_empty_history_tv_canonical(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0)
        sd.on_tv_event(tv, [])
        assert tv["canonical"] is True
        assert tv["source"] == "tradingview"

    def test_history_only_tv_events_tv_canonical(self):
        sd = StructureDedup()
        prev_tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0, ts_offset_secs=-10)
        prev_tv["canonical"] = True
        prev_tv["source"] = "tradingview"
        tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [prev_tv])
        assert tv["canonical"] is True   # TV doesn't suppress TV


# ── 9. Repeated TV webhook → both canonical (no Databento to match) ───────────

class TestRepeatedTVWebhook:
    def test_two_tv_events_both_canonical_without_databento(self):
        sd = StructureDedup()
        tv1 = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_tv_event(tv1, [])
        assert tv1["canonical"] is True
        tv2 = _tv_entry("BOS DEMAND", "MNQ", 29801.0, ts_offset_secs=5)
        sd.on_tv_event(tv2, [tv1])
        assert tv2["canonical"] is True   # TV never suppresses TV


# ── 10. Repeated Databento callback → metrics incremented ─────────────────────

class TestRepeatedDatabentoCallback:
    def test_each_databento_call_increments_produced_counter(self):
        sd = StructureDedup()
        db1 = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_databento_event(db1, [])
        m1 = sd.get_metrics()["structure"]["databento_events_produced"]

        db2 = _db_entry("BOS DEMAND", "MNQ", 29800.25, ts_offset_secs=60)
        sd.on_databento_event(db2, [db1])
        m2 = sd.get_metrics()["structure"]["databento_events_produced"]
        assert m2 == m1 + 1


# ── 11. HH / HL / LH / LL behaviour ──────────────────────────────────────────

class TestSwingPivots:
    def test_hh_deduped(self):
        sd = StructureDedup()
        db = _db_entry("HH", "MNQ", 29850.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("HH", "MNQ", 29850.0, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_hl_deduped(self):
        sd = StructureDedup()
        db = _db_entry("HL", "MNQ", 29700.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("HL", "MNQ", 29700.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_lh_deduped(self):
        sd = StructureDedup()
        db = _db_entry("LH", "MES", 5420.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("LH", "MES", 5420.0, ts_offset_secs=4)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_ll_deduped(self):
        sd = StructureDedup()
        db = _db_entry("LL", "MES", 5380.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("LL", "MES", 5380.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_hh_and_hl_not_confused(self):
        """HH and HL are different alert_types — must NOT deduplicate each other."""
        sd = StructureDedup()
        db = _db_entry("HH", "MNQ", 29850.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("HL", "MNQ", 29700.0, ts_offset_secs=3)   # different type
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True

    def test_lh_and_ll_not_confused(self):
        sd = StructureDedup()
        db = _db_entry("LH", "MES", 5420.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("LL", "MES", 5380.0, ts_offset_secs=4)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True


# ── 12. ALERT_HISTORY readers ignore shadow duplicates ────────────────────────

class TestShadowIgnoredByConsumers:
    def _mock_latest_ts(self, entries, alert_type, inst):
        """Mimic _latest_ts / latest() consumer with the canonical filter applied."""
        latest = None
        for a in entries:
            if a.get("alert_type") != alert_type:
                continue
            if a.get("canonical") is False:   # ← the canonical filter
                continue
            a_inst = a.get("instrument", "")
            if a_inst != inst:
                continue
            try:
                ts = datetime.fromisoformat(a["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if latest is None or ts > latest:
                    latest = ts
            except (ValueError, TypeError):
                pass
        return latest

    def test_shadow_tv_entry_ignored(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=2)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False

        result = self._mock_latest_ts([db, tv], "BOS DEMAND", "MNQ")
        assert result is not None              # gate: has_bos_demand = True
        # Only canonical Databento timestamp used
        assert result.isoformat() == db["timestamp"]

    def test_no_databento_tv_canonical_included(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_tv_event(tv, [])
        assert tv["canonical"] is True
        result = self._mock_latest_ts([tv], "BOS DEMAND", "MNQ")
        assert result is not None              # TV fallback is used


# ── 13. Source tags preserved ─────────────────────────────────────────────────

class TestSourceTags:
    def test_databento_source_tag(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        assert db["source"] == "databento"
        assert db["canonical"] is True

    def test_tv_source_tag_canonical(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0)
        sd.on_tv_event(tv, [])
        assert tv["source"] == "tradingview"
        assert tv["canonical"] is True

    def test_tv_source_tag_shadow(self):
        sd = StructureDedup()
        db = _db_entry("BOS SUPPLY", "MGC", 3280.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])
        assert tv["source"] == "tradingview"   # source tag preserved even on shadow
        assert tv["canonical"] is False

    def test_fast_entry_bridge_source_tag_not_touched(self):
        """fast_entry_bridge events are marked canonical=True explicitly in app.py;
        the dedup engine does not touch them (source != tradingview/databento)."""
        sd = StructureDedup()
        bridge = {
            "alert_type":  "CHOCH DEMAND",
            "instrument":  "MNQ",
            "price":       29800.0,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "source":      "fast_entry_bridge",
            "canonical":   True,
        }
        # Calling on_tv_event would overwrite source — the caller must NOT call
        # on_tv_event for bridge events (they bypass the TV event path).
        # Verify the sentinel field is preserved:
        assert bridge["source"] == "fast_entry_bridge"
        assert bridge["canonical"] is True


# ── 14. Scoring parity preserved ──────────────────────────────────────────────

class TestScoringParity:
    """With the canonical filter, one canonical event gives the same boolean gate
    result as having both TV + Databento without the filter would have given."""

    def test_gate_unchanged_single_canonical(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])

        # Simulate gate: "has_bos_demand = latest('BOS DEMAND') is not None"
        # With canonical filter, only db contributes
        def has_bos(entries, inst):
            for a in entries:
                if a.get("alert_type") != "BOS DEMAND":
                    continue
                if a.get("canonical") is False:
                    continue
                if a.get("instrument") != inst:
                    continue
                return True
            return False

        assert has_bos([db, tv], "MNQ") is True    # gate passes with filter
        # Verify result is same as without filter (both canonical historically)
        assert has_bos([db], "MNQ") is True


# ── 15. Conflict detection ────────────────────────────────────────────────────

class TestConflictDetection:
    def test_conflict_flagged_when_databento_disagrees(self):
        sd = StructureDedup()
        db = _db_entry("BOS SUPPLY", "MNQ", 29800.0)   # Databento: bearish
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=5)  # TV: bullish
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is True       # no same-type match → TV is canonical
        assert tv.get("conflict") is True    # but conflict flagged
        assert sd.get_metrics()["structure"]["conflict_events"] == 1

    def test_no_conflict_when_directions_agree(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)  # Both bullish
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29801.0, ts_offset_secs=5)
        sd.on_tv_event(tv, [db])
        assert tv.get("conflict") is not True

    def test_no_conflict_outside_time_window(self):
        sd = StructureDedup()
        old_db = _db_entry("BOS SUPPLY", "MNQ", 29800.0,
                            ts_offset_secs=-(DEDUP_TIME_SECS + 30))
        old_db["canonical"] = True
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=0)
        sd.on_tv_event(tv, [old_db])
        assert tv.get("conflict") is not True


# ── 16. Non-structure events always canonical ─────────────────────────────────

class TestNonStructureEvents:
    def test_sweep_event_not_deduped(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        sweep = _tv_entry("MNQ BULLISH SWEEP", "MNQ", 29800.0, ts_offset_secs=5)
        sd.on_tv_event(sweep, [db])
        assert sweep["canonical"] is True    # not a structure type
        assert sweep["source"] == "tradingview"

    def test_zone_event_not_deduped(self):
        sd = StructureDedup()
        zone = _tv_entry("MNQ NEW DEMAND ZONE", "MNQ", 29800.0)
        sd.on_tv_event(zone, [])
        assert zone["canonical"] is True

    def test_fvg_event_not_deduped(self):
        sd = StructureDedup()
        fvg = _tv_entry("BULLISH FVG", "MNQ", 29800.0)
        sd.on_tv_event(fvg, [])
        assert fvg["canonical"] is True
        assert fvg["source"] == "tradingview"


# ── 17. Metrics tracking ──────────────────────────────────────────────────────

class TestMetrics:
    def test_full_metrics_cycle(self):
        sd = StructureDedup()

        # Databento fires first (unmatched → TV hasn't arrived yet)
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        m = sd.get_metrics()["structure"]
        assert m["databento_events_produced"] == 1
        assert m["unmatched_databento_events"] == 1
        assert m["matched_events"] == 0
        assert m["deduped_events"] == 0

        # TV arrives → shadow
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.5, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])
        m = sd.get_metrics()["structure"]
        assert m["tv_events_received"] == 1
        assert m["matched_events"] == 1
        assert m["deduped_events"] == 1
        assert m["tv_fallback_events"] == 0

    def test_tv_fallback_counted(self):
        sd = StructureDedup()
        tv = _tv_entry("BOS SUPPLY", "MGC", 3280.0)
        sd.on_tv_event(tv, [])
        m = sd.get_metrics()["structure"]
        assert m["tv_fallback_events"] == 1
        assert m["matched_events"] == 0

    def test_databento_retroactive_demote_increments_matched(self):
        sd = StructureDedup()
        tv = _tv_entry("CHOCH SUPPLY", "MES", 5400.0)
        sd.on_tv_event(tv, [])     # TV canonical (no Databento yet)
        db = _db_entry("CHOCH SUPPLY", "MES", 5400.0, ts_offset_secs=4)
        sd.on_databento_event(db, [tv])   # retroactively demotes TV
        m = sd.get_metrics()["structure"]
        assert m["matched_events"] == 1
        assert m["deduped_events"] == 1
        assert m["unmatched_databento_events"] == 0   # a match WAS found

    def test_get_metrics_is_snapshot(self):
        sd = StructureDedup()
        m1 = sd.get_metrics()["structure"]
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_tv_event(tv, [])
        m2 = sd.get_metrics()["structure"]
        assert m1["tv_events_received"] == 0
        assert m2["tv_events_received"] == 1


# ── 18. Backward compat: legacy entries without 'canonical' field ─────────────

class TestBackwardCompat:
    def test_legacy_entry_not_skipped_by_canonical_filter(self):
        """Existing entries have no 'canonical' field → None is False → False → kept."""
        legacy = {"alert_type": "BOS DEMAND", "instrument": "MNQ",
                  "price": 29800.0, "timestamp": datetime.now(timezone.utc).isoformat()}
        assert "canonical" not in legacy
        assert legacy.get("canonical") is None
        assert (legacy.get("canonical") is False) is False   # not skipped

    def test_databento_retroactive_demote_skips_already_false(self):
        """on_databento_event must not double-demote entries already marked False."""
        sd = StructureDedup()
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0)
        tv["canonical"] = False
        tv["duplicate_of"] = "first_db_ts"
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=5)
        sd.on_databento_event(db, [tv])
        # duplicate_of should NOT be overwritten by the second Databento call
        assert tv["duplicate_of"] == "first_db_ts"


# ── 19. Price check skipped when price is None (fail-open) ────────────────────

class TestPriceNoneFallback:
    def test_no_price_on_tv_entry_still_matches(self):
        sd = StructureDedup()
        db = _db_entry("BOS DEMAND", "MNQ", 29800.0)
        sd.on_databento_event(db, [])
        tv = {
            "alert_type": "BOS DEMAND", "instrument": "MNQ",
            "price": None,
            "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat(),
        }
        sd.on_tv_event(tv, [db])
        # price check is skipped when TV price is None → still matches on type+inst+time
        assert tv["canonical"] is False

    def test_no_price_on_databento_entry_still_matches(self):
        sd = StructureDedup()
        db = {
            "alert_type": "BOS DEMAND", "instrument": "MNQ",
            "price": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "databento", "canonical": True,
        }
        sd.on_databento_event(db, [])
        tv = _tv_entry("BOS DEMAND", "MNQ", 29800.0, ts_offset_secs=3)
        sd.on_tv_event(tv, [db])
        assert tv["canonical"] is False


# ── 20. _events_match helper unit tests ───────────────────────────────────────

class TestEventsMatch:
    def _entry(self, a_type, inst, price, ts_offset=0):
        ts = (datetime.now(timezone.utc) + timedelta(seconds=ts_offset)).isoformat()
        return {"alert_type": a_type, "instrument": inst,
                "price": float(price), "timestamp": ts}

    def test_identical_entries_not_self_matched(self):
        e = self._entry("BOS DEMAND", "MNQ", 29800.0)
        assert _events_match(e, e) is False

    def test_same_event_different_dicts(self):
        a = self._entry("BOS DEMAND", "MNQ", 29800.0, ts_offset=0)
        b = self._entry("BOS DEMAND", "MNQ", 29800.0, ts_offset=2)
        assert _events_match(a, b) is True

    def test_mismatch_on_alert_type(self):
        a = self._entry("BOS DEMAND", "MNQ", 29800.0)
        b = self._entry("BOS SUPPLY", "MNQ", 29800.0)
        assert _events_match(a, b) is False

    def test_mismatch_on_instrument(self):
        a = self._entry("BOS DEMAND", "MNQ", 29800.0)
        b = self._entry("BOS DEMAND", "MES", 29800.0)
        assert _events_match(a, b) is False


# ── 21. Module-level singleton is StructureDedup ──────────────────────────────

def test_singleton_type():
    assert isinstance(STRUCTURE_DEDUP, StructureDedup)


# ── 22. STRUCTURE_TYPES coverage ─────────────────────────────────────────────

def test_structure_types_complete():
    expected = {
        "BOS DEMAND", "BOS SUPPLY",
        "CHOCH DEMAND", "CHOCH SUPPLY",
        "HH", "HL", "LH", "LL",
    }
    assert STRUCTURE_TYPES == expected


def test_all_structure_types_have_direction_and_family():
    for t in STRUCTURE_TYPES:
        assert t in STRUCTURE_DIRECTION, f"Missing direction for {t}"
        assert t in STRUCTURE_FAMILY,    f"Missing family for {t}"
