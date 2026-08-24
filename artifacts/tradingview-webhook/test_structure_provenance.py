"""Focused tests for the shadow-only Databento structure provenance trace."""

from __future__ import annotations

from collections import deque
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from databento_brain import (  # noqa: E402
    DatabentoBrain,
    annotate_structure_provenance,
    clear_structure_provenance,
    get_structure_provenance,
)


def _brain():
    return DatabentoBrain(
        alert_history=deque(maxlen=100),
        cvd_by_ticker={},
        rvol_by_ticker={},
        auto_price_by_ticker={},
        current_price_by_ticker={},
        current_price_ts_by_ticker={},
        volume_spike_by_ticker={},
    )


def _bars(close=15.0):
    """Twelve bars confirm the center high with SWING_N=5."""
    bars = []
    for index in range(12):
        bars.append({
            "ts": 1_700_000_000 + index * 60,
            "open": 10.0,
            "high": 10.0,
            "low": 8.0,
            "close": 10.0,
            "volume": 1,
        })
    bars[6].update({"high": 20.0, "low": 6.0, "close": 12.0})
    bars[5]["low"] = 0.0  # center bar is not also a swing low
    bars[-1]["close"] = close
    return bars


def setup_function():
    clear_structure_provenance()


def test_insufficient_history_is_explicit_not_a_negative_break():
    brain = _brain()
    brain._detect_structure("MNQ", _bars()[:11])

    trace = get_structure_provenance("MNQ", limit=1)[0]
    assert trace["shadow_only"] is True
    assert trace["history"] == {
        "available": False,
        "bars": 11,
        "required": 12,
        "reason": "insufficient_completed_bars_for_pivot_confirmation",
    }
    assert trace["raw_decisions"] == [{
        "alert_type": None,
        "candidate": None,
        "decision": "unavailable",
        "reason": "insufficient_completed_bars_for_pivot_confirmation",
    }]
    assert trace["pivot"]["side"] is None
    assert trace["structure_gate"] is None


def test_confirmed_pivot_records_raw_break_rejection_reason():
    brain = _brain()
    brain._detect_structure("MNQ", _bars(close=19.75))

    trace = get_structure_provenance("MNQ", limit=1)[0]
    assert trace["history"]["available"] is True
    assert trace["pivot"]["side"] == "high"
    assert trace["pivot"]["level"] == 20.0
    assert trace["pivot"]["age_bars"] == 5
    assert trace["confirmation_progress"]["left_bars"] == 5
    assert trace["confirmation_progress"]["right_bars"] == 5
    assert trace["raw_decisions"] == [{
        "alert_type": "BOS DEMAND",
        "candidate": "demand",
        "decision": "reject",
        "reason": "close_not_above_confirmed_swing_high",
        "pivot_level": 20.0,
        "close": 19.75,
    }]


def test_prior_bear_trend_accepts_choch_without_changing_detector_semantics():
    brain = _brain()
    brain._trend["MNQ"] = "bear"
    brain._detect_structure("MNQ", _bars(close=20.25))

    trace = get_structure_provenance("MNQ", limit=1)[0]
    assert trace["prior_trend"] == "bear"
    assert trace["raw_decisions"][0] == {
        "alert_type": "CHOCH DEMAND",
        "candidate": "demand",
        "decision": "accept",
        "reason": "confirmed_pivot_break",
    }
    assert trace["dedupe"][0]["outcome"] == "new_level"
    assert brain._ah[-1]["alert_type"] == "CHOCH DEMAND"
    assert brain._trend["MNQ"] == "bull"


def test_duplicate_break_is_exposed_without_another_alert():
    brain = _brain()
    brain._last_bos["MNQ"] = {"type": "BOS DEMAND", "level": 20.0}
    brain._detect_structure("MNQ", _bars(close=20.25))

    trace = get_structure_provenance("MNQ", limit=1)[0]
    assert trace["raw_decisions"][0]["decision"] == "reject"
    assert trace["raw_decisions"][0]["reason"] == "same_break_level_already_emitted"
    assert trace["dedupe"][0]["outcome"] == "duplicate"
    assert list(brain._ah) == []


def test_authoritative_cycle_and_gate_are_copied_to_the_exact_trace():
    brain = _brain()
    brain._detect_structure("MNQ", _bars(close=19.75))
    trace_id = get_structure_provenance("MNQ", limit=1)[0]["trace_id"]
    cycle = {
        "instrument": "MNQ",
        "state": "NO_STRUCTURE",
        "direction": None,
        "confirmed": False,
    }
    gate = {
        "source": "full_analysis",
        "Long": {"structure_confirmed": False},
        "Short": {"structure_confirmed": True},
    }

    assert annotate_structure_provenance(
        "MNQ", trace_id, structure_cycle=cycle, gate_result=gate
    ) is True
    trace = get_structure_provenance("MNQ", limit=1)[0]
    assert trace["resolved_structure_cycle"] == cycle
    assert trace["structure_gate"] == gate
    assert trace["analysis_attached"] is True
    assert brain._last_bos["MNQ"] is None


def test_out_of_order_analysis_cannot_attach_to_a_newer_bar_trace():
    brain = _brain()
    first_bars = _bars(close=19.75)
    brain._detect_structure("MNQ", first_bars)
    first_trace_id = get_structure_provenance("MNQ", limit=1)[0]["trace_id"]

    # Replayed/malformed input can reuse a bar timestamp; trace identity must
    # still prevent a slow earlier analysis from being attached to this record.
    brain._detect_structure("MNQ", _bars(close=19.75))
    second_trace_id = get_structure_provenance("MNQ", limit=1)[0]["trace_id"]
    assert first_trace_id != second_trace_id

    first_cycle = {"instrument": "MNQ", "state": "FIRST_BAR"}
    second_cycle = {"instrument": "MNQ", "state": "SECOND_BAR"}
    assert annotate_structure_provenance(
        "MNQ", first_trace_id, structure_cycle=first_cycle
    ) is True
    assert annotate_structure_provenance(
        "MNQ", second_trace_id, structure_cycle=second_cycle
    ) is True

    traces = get_structure_provenance("MNQ", limit=2)
    assert traces[0]["trace_id"] == first_trace_id
    assert traces[0]["resolved_structure_cycle"] == first_cycle
    assert traces[1]["trace_id"] == second_trace_id
    assert traces[1]["resolved_structure_cycle"] == second_cycle


def test_read_only_endpoint_defaults_to_mnq_and_bounds_limit():
    brain = _brain()
    brain._detect_structure("MNQ", _bars(close=19.75))

    import app  # noqa: PLC0415

    client = app.app.test_client()
    response = client.get("/structure-provenance?limit=1")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["shadow_only"] is True
    assert payload["instrument"] == "MNQ"
    assert payload["count"] == 1
    assert payload["records"][0]["history"]["available"] is True


def test_read_only_endpoint_rejects_unknown_instrument():
    import app  # noqa: PLC0415

    response = app.app.test_client().get("/structure-provenance?instrument=BAD")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False