"""Focused sequence tests for the pure, state-aware BOS/CHOCH contract."""

from datetime import datetime, timedelta, timezone

from structure_state import STRUCTURE_CANDIDATE_POINTS, STRUCTURE_POINTS, resolve_structure_cycle


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def event(alert_type, minutes_ago=0, instrument="MNQ", **extra):
    return {
        "alert_type": alert_type,
        "instrument": instrument,
        "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        **extra,
    }


def resolve(events, **kwargs):
    return resolve_structure_cycle(events, "MNQ", now=NOW, window_minutes=20, **kwargs)


def test_first_choch_is_reversal_candidate_without_score_credit():
    state = resolve([event("CHOCH DEMAND", 1)])
    assert state["state"] == "REVERSAL_CANDIDATE"
    assert state["direction"] == "Long"
    assert state["confirmed"] is False
    assert state["allocation_points"] == STRUCTURE_CANDIDATE_POINTS
    assert state["next_event"] == "BOS DEMAND"


def test_bos_after_same_direction_choch_confirms_single_cycle_credit():
    state = resolve([event("CHOCH DEMAND", 4), event("BOS DEMAND", 1)])
    assert state["state"] == "REVERSAL_CONFIRMED"
    assert state["confirmed"] is True
    assert state["allocation_points"] == STRUCTURE_POINTS
    assert state["next_event"] == "CHOCH SUPPLY"


def test_bos_continuation_is_confirmed_without_a_prior_choch():
    state = resolve([event("BOS SUPPLY", 1)])
    assert state["state"] == "TREND_CONFIRMED"
    assert state["direction"] == "Short"
    assert state["allocation_points"] == STRUCTURE_POINTS


def test_multiple_bos_events_never_double_count_a_confirmed_cycle():
    state = resolve([
        event("CHOCH DEMAND", 8),
        event("BOS DEMAND", 5),
        event("BOS DEMAND", 3),
        event("BOS DEMAND", 1),
    ])
    assert state["state"] == "TREND_CONFIRMED"
    assert state["events_in_cycle"] == 4
    assert state["allocation_points"] == STRUCTURE_POINTS


def test_new_opposite_choch_supersedes_old_confirmed_cycle():
    state = resolve([
        event("CHOCH DEMAND", 10),
        event("BOS DEMAND", 7),
        event("CHOCH SUPPLY", 1),
    ])
    assert state["state"] == "REVERSAL_CANDIDATE"
    assert state["direction"] == "Short"
    assert state["confirmed"] is False
    assert state["allocation_points"] == STRUCTURE_CANDIDATE_POINTS
    assert state["next_event"] == "BOS SUPPLY"
    assert state["superseded_events"] == 2


def test_opposite_bos_without_its_own_choch_is_ignored():
    state = resolve([event("BOS DEMAND", 4), event("BOS SUPPLY", 1)])
    assert state["direction"] == "Long"
    assert state["state"] == "TREND_CONFIRMED"
    assert state["last_event"] == "BOS DEMAND"


def test_old_other_instrument_noncanonical_and_expired_events_cannot_affect_cycle():
    state = resolve([
        event("BOS DEMAND", 1, instrument="MGC"),
        event("BOS DEMAND", 1, canonical=False),
        event("BOS SUPPLY", 25),
    ])
    assert state["state"] == "NO_STRUCTURE"
    assert state["allocation_points"] == 0