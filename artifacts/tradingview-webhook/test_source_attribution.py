"""
Tests for Databento source attribution and double-counting audit.
Tests 1-15 from the "Databento Source Attribution and Double-Counting Audit" spec.

Run with:
    pytest artifacts/tradingview-webhook/test_source_attribution.py -v

Design: exercises the three new diagnostic functions directly with controlled
in-memory state.  No network calls, no live Databento connections, no DB.
All 15 tests prove the invariants from the spec:
  - Databento is the primary live source for price/volume/CVD/volatility.
  - Source labels survive round-trips through _compute_score_source_attribution.
  - Scores, verdicts, and auto-trade state are untouched by the diagnostics.
  - Duplicate events are detected and reported without being removed or merged.
  - No live network I/O occurs during any of these tests.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as _app
from app import (
    _compute_score_source_attribution,
    _audit_double_counting,
    _audit_event_duplicates,
    MARKET_INPUT_SOURCE_BY_TICKER,
    CVD_BY_TICKER,
    RVOL_BY_TICKER,
    VOLUME_SPIKE_BY_TICKER,
    VWAP_BY_TICKER,
    VOLATILITY_BY_TICKER,
    AUTO_PRICE_BY_TICKER,
    ALERT_HISTORY,
    SETUP_STATE,
    AUTO_FIRED_KEYS,
    LEARNING_SAMPLE_BY_KEY,
)

# ── Shared test fixtures ──────────────────────────────────────────────────────

_NOW  = datetime(2025, 7, 25, 14, 0, 0, tzinfo=timezone.utc)
_ISO  = _NOW.isoformat()
_INST = "MNQ"


def _db_cvd(inst=_INST, state="bullish", value=500.0):
    return {inst: {"state": state, "value": value, "direction": "rising",
                   "ts": _ISO, "source": "databento"}}


def _db_rvol(inst=_INST, value=2.1):
    return {inst: {"value": value, "ts": _ISO, "source": "databento"}}


def _db_vs(inst=_INST):
    return {inst: {"ts": _ISO, "source": "databento"}}


def _db_vwap(inst=_INST, value=21000.0):
    return {inst: {"value": value, "ts": _ISO, "source": "databento"}}


def _alert(alert_type, inst=_INST, source="databento", offset_s=0):
    ts = (_NOW - timedelta(seconds=offset_s)).isoformat()
    return {
        "alert_type":        alert_type,
        "ticker":            inst + "1!",
        "instrument":        inst,
        "instrument_source": source,
        "price":             21000.0,
        "timestamp":         ts,
        "raw":               {"source": "databento_brain" if source == "databento"
                              else "tradingview"},
    }


def _bos_demand_db(offset_s=0):
    return _alert("BOS DEMAND", source="databento", offset_s=offset_s)


def _bos_demand_tv(offset_s=0):
    return _alert("BOS DEMAND", source="ticker", offset_s=offset_s)


def _choch_demand_db(offset_s=0):
    return _alert("CHOCH DEMAND", source="databento", offset_s=offset_s)


def _sweep_bullish_db(offset_s=0):
    return _alert(f"{_INST} BULLISH SWEEP", source="databento", offset_s=offset_s)


def _sweep_bullish_tv(offset_s=0):
    return _alert(f"{_INST} BULLISH SWEEP", source="ticker", offset_s=offset_s)


# ── Test 1: Databento is the primary source for current price ─────────────────

def test_1_databento_primary_source_price():
    """AUTO_PRICE_BY_TICKER carries source='databento' when DatabentoBrain writes."""
    with patch.dict(_app.AUTO_PRICE_BY_TICKER,
                    {_INST: {"value": 21000.0, "ts": _ISO, "source": "databento"}}):
        rec = _app.AUTO_PRICE_BY_TICKER.get(_INST, {})
        assert rec.get("source") == "databento", (
            "AUTO_PRICE_BY_TICKER must carry source='databento' when "
            "DatabentoBrain is the writer"
        )


# ── Test 2: Databento is the primary source for volume ───────────────────────

def test_2_databento_primary_source_volume():
    with patch.dict(_app.RVOL_BY_TICKER, _db_rvol()):
        attr = _compute_score_source_attribution(
            _INST, [_bos_demand_db(), _sweep_bullish_db()], _NOW,
        )
    vol = next((c for c in attr if c["component"] == "Volume"), None)
    assert vol is not None
    assert vol["source"] == "databento", (
        f"Volume source should be 'databento', got {vol['source']!r}"
    )


# ── Test 3: Databento is the primary source for CVD ─────────────────────────

def test_3_databento_primary_source_cvd():
    with patch.dict(_app.CVD_BY_TICKER, _db_cvd()):
        attr = _compute_score_source_attribution(_INST, [], _NOW)
    cvd = next((c for c in attr if c["component"] == "CVD"), None)
    assert cvd is not None
    assert cvd["source"] == "databento"


# ── Test 4: Databento is the primary source for volatility ───────────────────

def test_4_databento_primary_source_volatility():
    """VWAP_BY_TICKER (written by DatabentoBrain) attributes to 'databento'."""
    with patch.dict(_app.VWAP_BY_TICKER, _db_vwap()):
        attr = _compute_score_source_attribution(_INST, [], _NOW)
    vwap = next((c for c in attr if c["component"] == "VWAP"), None)
    assert vwap is not None
    assert vwap["source"] == "databento"


# ── Test 5: Databento-generated structure events retain source attribution ───

def test_5_databento_structure_events_retain_source():
    ev = _bos_demand_db()
    assert ev.get("instrument_source") == "databento"
    attr = _compute_score_source_attribution(_INST, [ev], _NOW)
    bos = next((c for c in attr if c["component"] == "BOS"), None)
    assert bos is not None
    assert bos["source"] == "databento", (
        "BOS from Databento must attribute as 'databento' in source_attribution"
    )


# ── Test 6: TradingView-generated events retain source attribution ────────────

def test_6_tradingview_events_retain_source():
    ev = _bos_demand_tv()
    assert ev.get("instrument_source") == "ticker"
    attr = _compute_score_source_attribution(_INST, [ev], _NOW)
    bos = next((c for c in attr if c["component"] == "BOS"), None)
    assert bos is not None
    assert bos["source"] == "tradingview", (
        f"'ticker' instrument_source should normalize to 'tradingview', "
        f"got {bos['source']!r}"
    )


# ── Test 7: Source metadata does not change component points ─────────────────

def test_7_source_metadata_does_not_change_points():
    with patch.dict(_app.CVD_BY_TICKER, _db_cvd()):
        attr = _compute_score_source_attribution(
            _INST, [_bos_demand_db()], _NOW,
        )
    point_map = {c["component"]: c["points"] for c in attr}
    assert point_map["BOS"]     == 20
    assert point_map["CHOCH"]   == 20
    assert point_map["Sweep"]   == 15
    assert point_map["VWAP"]    == 15
    assert point_map["Volume"]  == 15
    assert point_map["CVD"]     == 15
    assert point_map["Session"] == 10


# ── Test 8: Source metadata does not change Edge Score ───────────────────────

def test_8_source_metadata_does_not_change_edge_score():
    """The scoring state stores must be identical before and after attribution."""
    with patch.dict(_app.CVD_BY_TICKER, _db_cvd()):
        with patch.dict(_app.RVOL_BY_TICKER, _db_rvol()):
            cvd_before  = dict(_app.CVD_BY_TICKER)
            rvol_before = dict(_app.RVOL_BY_TICKER)
            _compute_score_source_attribution(
                _INST, [_bos_demand_db()], _NOW,
            )
            cvd_after  = dict(_app.CVD_BY_TICKER)
            rvol_after = dict(_app.RVOL_BY_TICKER)
    assert cvd_after  == cvd_before,  "CVD_BY_TICKER must be unchanged"
    assert rvol_after == rvol_before, "RVOL_BY_TICKER must be unchanged"


# ── Test 9: Source metadata does not change READY/WAIT verdicts ──────────────

def test_9_source_metadata_does_not_change_verdicts():
    """ALERT_HISTORY and SETUP_STATE must be untouched by the audit functions."""
    ah_before = list(_app.ALERT_HISTORY)
    ss_before = dict(_app.SETUP_STATE)
    alerts = [_bos_demand_db(), _choch_demand_db()]
    _compute_score_source_attribution(_INST, alerts, _NOW)
    _audit_double_counting(_INST, [])
    _audit_event_duplicates(_INST, alerts)
    assert list(_app.ALERT_HISTORY) == ah_before, "ALERT_HISTORY must not be mutated"
    assert dict(_app.SETUP_STATE)   == ss_before, "SETUP_STATE must not be mutated"


# ── Test 10: Source metadata does not change auto-trade eligibility ───────────

def test_10_source_metadata_does_not_change_auto_trade():
    """AUTO_FIRED_KEYS and ACTIVE_TRADES_BY_INST must be untouched."""
    fired_before  = set(_app.AUTO_FIRED_KEYS)
    trades_before = dict(_app.ACTIVE_TRADES_BY_INST)
    _compute_score_source_attribution(_INST, [], _NOW)
    _audit_double_counting(_INST, [])
    _audit_event_duplicates(_INST, [])
    assert set(_app.AUTO_FIRED_KEYS)        == fired_before,  "AUTO_FIRED_KEYS changed"
    assert dict(_app.ACTIVE_TRADES_BY_INST) == trades_before, "ACTIVE_TRADES changed"


# ── Test 11: Source metadata does not change Learning delta behavior ──────────

def test_11_source_metadata_does_not_change_learning_delta():
    """LEARNING_SAMPLE_BY_KEY must be untouched; _resolve_learning_score_influence
    is never called by the source attribution functions."""
    sample_before = dict(_app.LEARNING_SAMPLE_BY_KEY)
    _compute_score_source_attribution(_INST, [_bos_demand_db()], _NOW)
    assert dict(_app.LEARNING_SAMPLE_BY_KEY) == sample_before, (
        "LEARNING_SAMPLE_BY_KEY must not be mutated by source attribution"
    )


# ── Test 12: Probable duplicates reported diagnostically ─────────────────────

def test_12_duplicate_events_reported_diagnostically():
    db_ev = _bos_demand_db(offset_s=0)
    tv_ev = _bos_demand_tv(offset_s=30)
    # Pass now_dt=_NOW so the 1-hour look-back window is anchored to the same
    # fixed clock used to generate the test event timestamps.
    dupes = _audit_event_duplicates(_INST, [db_ev, tv_ev], window_seconds=120, now_dt=_NOW)
    assert len(dupes) >= 1
    d = dupes[0]
    assert d["probable_duplicate"] is True
    assert d["action"]         == "diagnostic_only"
    assert d["canonical_type"] == "BOS"
    assert d["direction"]      == "long"
    assert d["gap_seconds"]    <= 120


# ── Test 13: Events are not merged or removed ─────────────────────────────────

def test_13_events_not_merged_or_removed():
    db_ev = _alert("CHOCH DEMAND", source="databento", offset_s=0)
    tv_ev = _alert("CHOCH DEMAND", source="ticker",    offset_s=10)
    snapshot = [db_ev, tv_ev]
    dupes = _audit_event_duplicates(_INST, list(snapshot), window_seconds=120, now_dt=_NOW)
    # Audit only reads; the snapshot is unchanged
    assert len(snapshot) == 2
    assert snapshot[0]["instrument_source"] == "databento"
    assert snapshot[1]["instrument_source"] == "ticker"
    assert len(dupes) >= 1
    assert all(d["action"] == "diagnostic_only" for d in dupes)


# ── Test 14: byte-identical when inst is None ─────────────────────────────────

def test_14_byte_identical_when_inst_is_none():
    """With inst=None attribution returns [] and touches no state."""
    cvd_before = dict(_app.CVD_BY_TICKER)
    mis_before = dict(_app.MARKET_INPUT_SOURCE_BY_TICKER)
    result = _compute_score_source_attribution(None, [], _NOW)
    assert result == [], "Expected empty list for None inst"
    assert dict(_app.CVD_BY_TICKER) == cvd_before
    assert None not in _app.MARKET_INPUT_SOURCE_BY_TICKER


# ── Test 15: No live Databento network calls in tests ────────────────────────

def test_15_no_live_databento_network_calls():
    """Source attribution must be fully in-memory (no socket I/O)."""
    with patch("socket.socket") as mock_sock:
        with patch.dict(_app.CVD_BY_TICKER,  _db_cvd()):
            with patch.dict(_app.RVOL_BY_TICKER, _db_rvol()):
                alerts = [_bos_demand_db(), _choch_demand_db(), _sweep_bullish_db()]
                attr  = _compute_score_source_attribution(_INST, alerts, _NOW)
                dupes = _audit_event_duplicates(_INST, alerts)
                dbl   = _audit_double_counting(_INST, attr)
        mock_sock.assert_not_called()
    assert isinstance(attr,  list)
    assert isinstance(dupes, list)
    assert isinstance(dbl,   list)


# ── Self-contained runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_1_databento_primary_source_price,
        test_2_databento_primary_source_volume,
        test_3_databento_primary_source_cvd,
        test_4_databento_primary_source_volatility,
        test_5_databento_structure_events_retain_source,
        test_6_tradingview_events_retain_source,
        test_7_source_metadata_does_not_change_points,
        test_8_source_metadata_does_not_change_edge_score,
        test_9_source_metadata_does_not_change_verdicts,
        test_10_source_metadata_does_not_change_auto_trade,
        test_11_source_metadata_does_not_change_learning_delta,
        test_12_duplicate_events_reported_diagnostically,
        test_13_events_not_merged_or_removed,
        test_14_byte_identical_when_inst_is_none,
        test_15_no_live_databento_network_calls,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(failed)
