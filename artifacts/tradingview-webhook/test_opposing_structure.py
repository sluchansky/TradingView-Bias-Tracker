"""Regression coverage for state-aware opposing BOS/CHOCH sequences.

The former test documented two independent, competing structure booleans.  The
strict gate now owns one ordered active cycle instead: a new opposite CHOCH
supersedes the old cycle and must be confirmed by a same-direction BOS.
"""

from datetime import datetime, timedelta, timezone

from structure_state import resolve_structure_cycle


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _event(alert_type, offset_minutes, instrument="MGC"):
    return {
        "alert_type": alert_type,
        "instrument": instrument,
        "timestamp": (NOW - timedelta(minutes=offset_minutes)).isoformat(),
    }


def _resolve(events, instrument="MGC"):
    return resolve_structure_cycle(events, instrument, now=NOW, window_minutes=10)


def test_new_opposite_choch_supersedes_a_prior_confirmed_cycle():
    state = _resolve([
        _event("CHOCH DEMAND", 8),
        _event("BOS DEMAND", 6),
        _event("CHOCH SUPPLY", 2),
    ])
    assert state["state"] == "REVERSAL_CANDIDATE"
    assert state["direction"] == "Short"
    assert state["confirmed"] is False
    assert state["allocation_points"] == 20
    assert state["next_event"] == "BOS SUPPLY"


def test_fresh_bos_confirms_the_active_reversal_not_the_old_cycle():
    state = _resolve([
        _event("CHOCH DEMAND", 9),
        _event("BOS DEMAND", 7),
        _event("CHOCH SUPPLY", 3),
        _event("BOS SUPPLY", 1),
    ])
    assert state["state"] == "REVERSAL_CONFIRMED"
    assert state["direction"] == "Short"
    assert state["confirmed"] is True
    assert state["allocation_points"] == 40
    assert state["events_in_cycle"] == 2


def test_duplicate_choch_does_not_refresh_or_double_credit_the_cycle():
    state = _resolve([
        _event("CHOCH DEMAND", 8),
        _event("CHOCH DEMAND", 1),
    ])
    assert state["state"] == "REVERSAL_CANDIDATE"
    assert state["allocation_points"] == 20
    assert state["cycle_started_at"].startswith((NOW - timedelta(minutes=8)).isoformat())


def test_other_instrument_events_never_enter_this_cycle():
    state = _resolve([
        _event("BOS DEMAND", 1, "MNQ"),
    ])
    assert state["state"] == "NO_STRUCTURE"