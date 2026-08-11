"""test_sweep_dedup.py
Tests for the sweep-event deduplication path added to structure_dedup.py.

All existing structure tests remain in test_structure_dedup.py.
These tests cover only the SWEEP family extension.

Coverage:
  • TV bullish/bearish sweep alone  (canonical fallback)
  • Databento bullish/bearish sweep alone
  • Databento-first → TV duplicate
  • TV-first → Databento retroactive demote
  • Within / outside time tolerance
  • Within / outside price tolerance
  • Opposite-direction conflict
  • Per-instrument tick scaling (MGC / MNQ / MES / MYM)
  • Missing price fail-open
  • Canonical filter semantics: duplicate does NOT count in gate/edge reads
  • LIQUIDITY_SWEEP_REVERSAL: duplicate sweep does NOT create duplicate setup
  • Chart: retains both source records (canonical and shadow)
  • Metrics: structure and sweep counters stay independent
  • Structure BOS dedup regression: existing behaviour unchanged
  • Singleton type check
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from structure_dedup import (
    STRUCTURE_DEDUP,
    STRUCTURE_TYPES,
    SWEEP_TYPES,
    ALL_DEDUP_TYPES,
    SWEEP_DIRECTION,
    SWEEP_FAMILY,
    DEDUP_TIME_SECS,
    DEDUP_PRICE_TICKS,
    INSTRUMENT_TICK_SIZE,
    StructureDedup,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offset_iso(secs: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()


def _tv_sweep(inst: str, direction: str, price: float = 100.0, ts_offset: float = 0.0) -> dict:
    """Build a TradingView sweep event record (NOT yet tagged by dedup engine)."""
    return {
        "alert_type":        f"{inst} {direction} SWEEP",
        "instrument":        inst,
        "instrument_source": "tradingview",
        "price":             price,
        "timestamp":         _offset_iso(ts_offset),
    }


def _db_sweep(inst: str, direction: str, price: float = 100.0, ts_offset: float = 0.0) -> dict:
    """Build a Databento sweep event record (canonical=True already from _inject_alert)."""
    return {
        "alert_type":        f"{inst} {direction} SWEEP",
        "instrument":        inst,
        "instrument_source": "databento",
        "source":            "databento",
        "canonical":         True,
        "price":             price,
        "timestamp":         _offset_iso(ts_offset),
    }


def _fresh_engine() -> StructureDedup:
    """Return a fresh StructureDedup with zeroed counters for isolation."""
    return StructureDedup()


# ── Module-level taxonomy ─────────────────────────────────────────────────────

class TestSweepTaxonomy:
    def test_sweep_types_count(self):
        # 4 instruments × 2 directions = 8 types
        assert len(SWEEP_TYPES) == 8

    def test_all_instruments_present(self):
        for inst in ("MGC", "MNQ", "MES", "MYM"):
            assert f"{inst} BULLISH SWEEP" in SWEEP_TYPES
            assert f"{inst} BEARISH SWEEP" in SWEEP_TYPES

    def test_sweep_direction_map(self):
        for inst in ("MGC", "MNQ", "MES", "MYM"):
            assert SWEEP_DIRECTION[f"{inst} BULLISH SWEEP"] == "BULLISH"
            assert SWEEP_DIRECTION[f"{inst} BEARISH SWEEP"] == "BEARISH"

    def test_sweep_family_map(self):
        for t in SWEEP_TYPES:
            assert SWEEP_FAMILY[t] == "SWEEP"

    def test_all_dedup_types_union(self):
        assert STRUCTURE_TYPES.issubset(ALL_DEDUP_TYPES)
        assert SWEEP_TYPES.issubset(ALL_DEDUP_TYPES)

    def test_structure_and_sweep_disjoint(self):
        assert STRUCTURE_TYPES.isdisjoint(SWEEP_TYPES)


# ── TV sweep alone (canonical fallback) ───────────────────────────────────────

class TestTVSweepAlone:
    def test_tv_bullish_sweep_canonical_when_no_databento(self):
        eng = _fresh_engine()
        rec = _tv_sweep("MNQ", "BULLISH", price=21000.0)
        eng.on_tv_event(rec, [])
        assert rec["source"] == "tradingview"
        assert rec["canonical"] is True
        assert "duplicate_of" not in rec

    def test_tv_bearish_sweep_canonical_when_no_databento(self):
        eng = _fresh_engine()
        rec = _tv_sweep("MGC", "BEARISH", price=2100.0)
        eng.on_tv_event(rec, [])
        assert rec["canonical"] is True

    def test_tv_bullish_fallback_increments_sweep_counter(self):
        eng = _fresh_engine()
        rec = _tv_sweep("MES", "BULLISH")
        eng.on_tv_event(rec, [])
        m = eng.get_metrics()
        assert m["sweep"]["tv_fallback_events"] == 1
        assert m["structure"]["tv_fallback_events"] == 0

    def test_tv_bearish_fallback_increments_sweep_counter(self):
        eng = _fresh_engine()
        rec = _tv_sweep("MYM", "BEARISH")
        eng.on_tv_event(rec, [])
        m = eng.get_metrics()
        assert m["sweep"]["tv_fallback_events"] == 1


# ── Databento sweep alone ─────────────────────────────────────────────────────

class TestDatabentSweepAlone:
    def test_databento_bullish_sweep_canonical(self):
        eng = _fresh_engine()
        rec = _db_sweep("MNQ", "BULLISH")
        eng.on_databento_event(rec, [])
        assert rec["canonical"] is True
        assert rec["source"] == "databento"

    def test_databento_bearish_sweep_canonical(self):
        eng = _fresh_engine()
        rec = _db_sweep("MGC", "BEARISH")
        eng.on_databento_event(rec, [])
        assert rec["canonical"] is True

    def test_databento_sweep_increments_produced_counter(self):
        eng = _fresh_engine()
        rec = _db_sweep("MNQ", "BULLISH")
        eng.on_databento_event(rec, [])
        m = eng.get_metrics()
        assert m["sweep"]["databento_events_produced"] == 1
        assert m["structure"]["databento_events_produced"] == 0

    def test_databento_unmatched_increments_unmatched_counter(self):
        eng = _fresh_engine()
        rec = _db_sweep("MES", "BULLISH")
        eng.on_databento_event(rec, [])
        m = eng.get_metrics()
        assert m["sweep"]["unmatched_databento_events"] == 1


# ── Databento first → TV arrives as duplicate ─────────────────────────────────

class TestDatabentFirstTVDuplicate:
    def test_tv_sweep_shadowed_when_databento_already_in_history(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-10)
        # Databento fired first (already in history)
        history = [db]
        tv = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, history)
        assert tv["canonical"] is False
        assert tv["duplicate_of"] == db["timestamp"]
        assert tv["source"] == "tradingview"

    def test_tv_shadow_increments_sweep_matched_counter(self):
        eng = _fresh_engine()
        db = _db_sweep("MGC", "BEARISH", price=2100.0, ts_offset=-5)
        tv = _tv_sweep("MGC", "BEARISH", price=2100.5, ts_offset=0)
        eng.on_tv_event(tv, [db])
        m = eng.get_metrics()
        assert m["sweep"]["matched_events"] == 1
        assert m["sweep"]["deduped_events"] == 1


# ── TV first → Databento retroactively demotes ────────────────────────────────

class TestTVFirstDatabentoDemotes:
    def test_databento_retroactively_demotes_tv_in_history(self):
        eng = _fresh_engine()
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-10)
        tv["canonical"] = True   # TV arrived first, was canonical
        db = _db_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        # history snapshot taken BEFORE databento append (contains TV)
        eng.on_databento_event(db, [tv])
        assert tv["canonical"] is False
        assert tv["duplicate_of"] == db["timestamp"]
        assert db["canonical"] is True

    def test_retroactive_demote_increments_sweep_matched_counter(self):
        eng = _fresh_engine()
        tv = _tv_sweep("MGC", "BEARISH", price=2100.0, ts_offset=-5)
        tv["canonical"] = True
        db = _db_sweep("MGC", "BEARISH", price=2100.2, ts_offset=0)
        eng.on_databento_event(db, [tv])
        m = eng.get_metrics()
        assert m["sweep"]["matched_events"] == 1
        assert m["sweep"]["deduped_events"] == 1
        assert m["sweep"]["unmatched_databento_events"] == 0


# ── Time tolerance ────────────────────────────────────────────────────────────

class TestSweepTimeWindow:
    def test_within_tolerance_deduped(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-(DEDUP_TIME_SECS - 1))
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_outside_tolerance_not_deduped(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-(DEDUP_TIME_SECS + 1))
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True


# ── Price tolerance (per-instrument tick scaling) ─────────────────────────────

class TestSweepPriceTolerance:
    def _tol(self, inst: str) -> float:
        return INSTRUMENT_TICK_SIZE[inst] * DEDUP_PRICE_TICKS

    def test_mgc_within_tolerance(self):
        eng = _fresh_engine()
        tol = self._tol("MGC")
        db = _db_sweep("MGC", "BULLISH", price=2100.0)
        tv = _tv_sweep("MGC", "BULLISH", price=2100.0 + tol - 0.01)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_mgc_beyond_tolerance(self):
        eng = _fresh_engine()
        tol = self._tol("MGC")
        db = _db_sweep("MGC", "BULLISH", price=2100.0)
        tv = _tv_sweep("MGC", "BULLISH", price=2100.0 + tol + 0.01)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True

    def test_mnq_within_tolerance(self):
        eng = _fresh_engine()
        tol = self._tol("MNQ")
        db = _db_sweep("MNQ", "BEARISH", price=21000.0)
        tv = _tv_sweep("MNQ", "BEARISH", price=21000.0 + tol - 0.01)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_mnq_beyond_tolerance(self):
        eng = _fresh_engine()
        tol = self._tol("MNQ")
        db = _db_sweep("MNQ", "BEARISH", price=21000.0)
        tv = _tv_sweep("MNQ", "BEARISH", price=21000.0 + tol + 0.01)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True

    def test_mes_tick_scaling(self):
        eng = _fresh_engine()
        tol = self._tol("MES")
        db = _db_sweep("MES", "BULLISH", price=5000.0)
        tv = _tv_sweep("MES", "BULLISH", price=5000.0 + tol - 0.01)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_mym_tick_scaling(self):
        eng = _fresh_engine()
        tol = self._tol("MYM")
        db = _db_sweep("MYM", "BEARISH", price=38000.0)
        tv = _tv_sweep("MYM", "BEARISH", price=38000.0 + tol - 0.1)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False


# ── Missing price fail-open ───────────────────────────────────────────────────

class TestSweepMissingPriceFailOpen:
    def test_no_price_on_tv_still_matches(self):
        """Missing TV price → price check skipped → match on type+inst+time."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=None)
        tv.pop("price")   # entirely absent
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

    def test_no_price_on_databento_still_matches(self):
        """Missing Databento price → price check skipped → TV gets shadowed."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", ts_offset=-5)
        db.pop("price")
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False


# ── Opposite-direction conflict ───────────────────────────────────────────────

class TestSweepConflictDetection:
    def test_conflict_flagged_when_databento_disagrees(self):
        """Databento BULLISH sweep + TV BEARISH sweep → TV marked conflict=True."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        db["canonical"] = True
        tv = _tv_sweep("MNQ", "BEARISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True   # not a duplicate — different direction
        assert tv.get("conflict") is True

    def test_no_conflict_same_direction(self):
        eng = _fresh_engine()
        # No Databento at all — TV should be clean fallback, no conflict
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0)
        eng.on_tv_event(tv, [])
        assert tv.get("conflict") is None

    def test_conflict_increments_sweep_counter(self):
        eng = _fresh_engine()
        db = _db_sweep("MGC", "BULLISH", price=2100.0, ts_offset=-5)
        db["canonical"] = True
        tv = _tv_sweep("MGC", "BEARISH", price=2100.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        m = eng.get_metrics()
        assert m["sweep"]["conflict_events"] == 1

    def test_no_conflict_outside_time_window(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-(DEDUP_TIME_SECS + 5))
        db["canonical"] = True
        tv = _tv_sweep("MNQ", "BEARISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv.get("conflict") is None


# ── Cross-instrument isolation ────────────────────────────────────────────────

class TestSweepCrossInstrumentIsolation:
    def test_mnq_sweep_does_not_match_mgc_sweep(self):
        eng = _fresh_engine()
        db = _db_sweep("MGC", "BULLISH", price=2100.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True   # different instrument — no match

    def test_mes_sweep_does_not_match_mym_sweep(self):
        eng = _fresh_engine()
        db = _db_sweep("MES", "BEARISH", price=5000.0, ts_offset=-5)
        tv = _tv_sweep("MYM", "BEARISH", price=38000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is True


# ── Canonical filter semantics (gate / edge reads) ────────────────────────────

class TestCanonicalFilterSemantics:
    """Verify that the canonical=False flag produced by the dedup engine causes
    consumers to skip the record.  Tests simulate the filter pattern:
        if a.get("canonical") is False: continue
    """

    def test_shadow_sweep_is_skipped_by_canonical_filter(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False

        # Simulate a gate consumer that applies the canonical filter
        history = [db, tv]
        matching = [
            a for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False   # the canonical filter
        ]
        assert len(matching) == 1
        assert matching[0] is db

    def test_duplicate_sweep_counts_once_in_gate_freshness(self):
        """Both a Databento canonical and a TV shadow of the same MNQ sweep
        exist in history.  A consumer using the canonical filter sees exactly
        one matching entry → no double-freshness / double-confirmation."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, [db])

        history = [db, tv]
        count = sum(
            1 for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False
        )
        assert count == 1

    def test_duplicate_sweep_counts_once_in_edge_score(self):
        """has_bull_sweep with canonical filter = exactly 1 match, not 2."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=0)
        eng.on_tv_event(tv, [db])

        history = [db, tv]
        # Simulate _has() / has_bull_sweep logic with canonical filter
        bull_sweeps = [
            a for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False
        ]
        has_bull_sweep = len(bull_sweeps) > 0
        assert has_bull_sweep is True
        assert len(bull_sweeps) == 1   # NOT 2 — no double-scoring

    def test_fallback_tv_sweep_is_included_when_databento_absent(self):
        """When Databento hasn't fired, TV canonical=True must reach consumers."""
        eng = _fresh_engine()
        tv = _tv_sweep("MNQ", "BULLISH", price=21000.0)
        eng.on_tv_event(tv, [])
        assert tv["canonical"] is True

        # Simulate gate consumer — must see the fallback TV record
        history = [tv]
        matching = [
            a for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False
        ]
        assert len(matching) == 1

    def test_legacy_entry_not_skipped_by_canonical_filter(self):
        """Records with no 'canonical' key (legacy) return None for
        .get('canonical') → None is False evaluates to False → NOT skipped."""
        legacy = {
            "alert_type":  "MNQ BULLISH SWEEP",
            "instrument":  "MNQ",
            "timestamp":   _now_iso(),
            # no "canonical" key
        }
        assert legacy.get("canonical") is not False  # not skipped


# ── LIQUIDITY_SWEEP_REVERSAL: duplicate sweep does not create duplicate setup ──

class TestLiquiditySweepReversalNoDuplicate:
    def test_duplicate_sweep_does_not_create_duplicate_lsr_opportunity(self):
        """Both Databento canonical and TV shadow exist for MNQ BULLISH SWEEP.
        A strategy scanner that applies the canonical filter sees exactly one
        sweep event — cannot generate duplicate LSR setup candidates."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-10)
        tv = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, [db])

        assert tv["canonical"] is False
        assert db["canonical"] is True

        history = [db, tv]
        # LSR setup scanner: find canonical bull sweeps for this instrument
        lsr_candidates = [
            a for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False
        ]
        assert len(lsr_candidates) == 1, (
            "Duplicate sweep must not create 2 LSR candidates"
        )

    def test_databento_canonical_sweep_usable_without_tv(self):
        """Databento-only sweep is still usable by LSR scanner."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0)
        eng.on_databento_event(db, [])

        history = [db]
        lsr_candidates = [
            a for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
            and a.get("canonical") is not False
        ]
        assert len(lsr_candidates) == 1


# ── Chart: retains both source records ───────────────────────────────────────

class TestChartRetainsBothRecords:
    def test_chart_sees_databento_canonical_and_tv_shadow(self):
        """The chart endpoint does NOT apply the canonical filter — it shows all
        records for audit purposes.  Both Databento canonical and TV shadow must
        be present in the raw ALERT_HISTORY snapshot."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, [db])

        # Chart reads all matching alert_types (no canonical filter)
        history = [db, tv]
        chart_events = [
            {"type": a["alert_type"], "canonical": a.get("canonical", True),
             "source": a.get("source", "unknown")}
            for a in history
            if a.get("alert_type") == "MNQ BULLISH SWEEP"
        ]
        assert len(chart_events) == 2
        canonical_flags = {e["canonical"] for e in chart_events}
        assert True  in canonical_flags
        assert False in canonical_flags


# ── Metrics: structure and sweep counters independent ─────────────────────────

class TestMetricsIndependence:
    def test_sweep_events_do_not_increment_structure_counters(self):
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH")
        tv = _tv_sweep("MNQ", "BULLISH")
        eng.on_databento_event(db, [])
        eng.on_tv_event(tv, [db])
        m = eng.get_metrics()
        assert m["structure"]["tv_events_received"]        == 0
        assert m["structure"]["databento_events_produced"] == 0
        assert m["sweep"]["tv_events_received"]            == 1
        assert m["sweep"]["databento_events_produced"]     == 1

    def test_structure_events_do_not_increment_sweep_counters(self):
        eng = _fresh_engine()
        db_bos = {
            "alert_type": "BOS DEMAND", "instrument": "MNQ",
            "source": "databento", "canonical": True,
            "price": 21000.0, "timestamp": _now_iso(),
        }
        tv_bos = {
            "alert_type": "BOS DEMAND", "instrument": "MNQ",
            "price": 21000.5, "timestamp": _offset_iso(-5),
        }
        eng.on_databento_event(db_bos, [tv_bos])
        m = eng.get_metrics()
        assert m["sweep"]["databento_events_produced"] == 0
        assert m["structure"]["databento_events_produced"] == 1

    def test_get_metrics_returns_both_namespaces(self):
        eng = _fresh_engine()
        m = eng.get_metrics()
        assert "structure" in m
        assert "sweep"     in m
        for key in ("tv_events_received", "databento_events_produced",
                    "matched_events", "tv_fallback_events",
                    "unmatched_databento_events", "deduped_events",
                    "conflict_events"):
            assert key in m["structure"]
            assert key in m["sweep"]

    def test_full_sweep_metrics_cycle(self):
        """One complete cycle: DB fires, TV fires duplicate → verify all counters."""
        eng = _fresh_engine()
        db = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-10)
        tv = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)
        eng.on_databento_event(db, [tv])   # TV already in history
        m = eng.get_metrics()
        assert m["sweep"]["databento_events_produced"] == 1
        assert m["sweep"]["matched_events"]            == 1
        assert m["sweep"]["deduped_events"]            == 1

    def test_legacy_compat_flat_metrics_property(self):
        """The .metrics property (legacy shim) aggregates both families."""
        eng = _fresh_engine()
        eng.on_tv_event(_tv_sweep("MNQ", "BULLISH"), [])
        flat = eng.metrics
        assert "tv_events_received" in flat
        assert flat["tv_events_received"] == 1   # from sweep family


# ── Structure BOS regression ──────────────────────────────────────────────────

class TestStructureRegressionAfterSweepExtension:
    """Verify BOS/CHOCH dedup is byte-identical after the sweep extension."""

    def _bos_demand(self, inst: str = "MNQ", price: float = 21000.0,
                    source: str = "tradingview", ts_offset: float = 0.0) -> dict:
        return {
            "alert_type":        "BOS DEMAND",
            "instrument":        inst,
            "instrument_source": source,
            "source":            source,
            "price":             price,
            "timestamp":         _offset_iso(ts_offset),
        }

    def test_bos_dedup_still_works(self):
        eng = _fresh_engine()
        db = self._bos_demand(source="databento", ts_offset=-10)
        db["canonical"] = True
        tv = self._bos_demand(price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        assert tv["canonical"] is False
        assert tv["duplicate_of"] == db["timestamp"]

    def test_bos_metrics_in_structure_namespace(self):
        eng = _fresh_engine()
        db = self._bos_demand(source="databento", ts_offset=-10)
        db["canonical"] = True
        tv = self._bos_demand(price=21001.0, ts_offset=0)
        eng.on_tv_event(tv, [db])
        m = eng.get_metrics()
        assert m["structure"]["matched_events"] == 1
        assert m["sweep"]["matched_events"]     == 0

    def test_sweep_and_bos_in_same_cycle_independent(self):
        """Processing a sweep and a BOS event in the same cycle does not cross-contaminate."""
        eng = _fresh_engine()
        db_bos = self._bos_demand(source="databento", ts_offset=-5)
        db_bos["canonical"] = True
        tv_bos  = self._bos_demand(price=21000.5, ts_offset=0)
        db_swp  = _db_sweep("MNQ", "BULLISH", price=21000.0, ts_offset=-5)
        tv_swp  = _tv_sweep("MNQ", "BULLISH", price=21001.0, ts_offset=0)

        eng.on_tv_event(tv_bos, [db_bos])
        eng.on_tv_event(tv_swp, [db_swp])

        m = eng.get_metrics()
        assert m["structure"]["matched_events"] == 1
        assert m["sweep"]["matched_events"]     == 1
        assert tv_bos["canonical"]  is False
        assert tv_swp["canonical"]  is False


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_singleton_is_StructureDedup():
    assert isinstance(STRUCTURE_DEDUP, StructureDedup)


def test_sweep_types_all_have_direction_and_family():
    for t in SWEEP_TYPES:
        assert t in SWEEP_DIRECTION, f"Missing direction for {t}"
        assert t in SWEEP_FAMILY,   f"Missing family for {t}"
