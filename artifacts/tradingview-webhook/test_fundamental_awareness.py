"""Focused contract tests for Phase 1 fundamental awareness."""

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import fundamental_awareness as fa


def _event(title, at, **extra):
    return {
        "title": title,
        "country": "USD",
        "impact": "High",
        "dt": at,
        **extra,
    }


def test_event_window_classification_before_active_after():
    now = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    before = fa.build_fundamental_context(
        [_event("Core CPI m/m", now + timedelta(minutes=20))],
        fetched_at=now, now=now,
    )
    active = fa.build_fundamental_context(
        [_event("FOMC Statement", now)],
        fetched_at=now, now=now,
    )
    after = fa.build_fundamental_context(
        [_event("Advance GDP q/q", now - timedelta(minutes=10))],
        fetched_at=now, now=now,
    )

    assert (before["status"], before["event_phase"], before["minutes_to_event"]) == (
        "EVENT_RISK", "BEFORE", 20)
    assert (active["status"], active["event_phase"], active["minutes_to_event"]) == (
        "EVENT_RISK", "ACTIVE", 0)
    assert (after["status"], after["event_phase"], after["minutes_to_event"]) == (
        "EVENT_RISK", "AFTER", -10)
    assert all(ctx["shadow_only"] is True for ctx in (before, active, after))


def test_america_new_york_dst_window_handles_spring_and_fall_offsets():
    # March 9 is EDT (UTC-4); November 2 is EST (UTC-5).
    spring_now = datetime.fromisoformat("2026-03-09T09:05:00-04:00")
    spring_event = datetime.fromisoformat("2026-03-09T09:30:00-04:00")
    fall_now = datetime.fromisoformat("2026-11-02T09:05:00-05:00")
    fall_event = datetime.fromisoformat("2026-11-02T09:30:00-05:00")

    spring = fa.build_fundamental_context(
        [_event("Employment Situation", spring_event)], fetched_at=spring_now, now=spring_now)
    fall = fa.build_fundamental_context(
        [_event("Employment Situation", fall_event)], fetched_at=fall_now, now=fall_now)

    assert spring["event_phase"] == fall["event_phase"] == "BEFORE"
    assert spring["minutes_to_event"] == fall["minutes_to_event"] == 25
    assert spring["scheduled_at"].endswith("+00:00")
    assert fall["scheduled_at"].endswith("+00:00")


def test_stale_and_unavailable_provider_fail_open_to_unknown():
    now = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    stale = fa.build_fundamental_context([], fetched_at=now - timedelta(hours=2), now=now, stale=True)
    unavailable = fa.build_fundamental_context(None, fetched_at=None, now=now, provider_error="HTTP 503")
    epoch_cache = fa.build_fundamental_context([], fetched_at=now.timestamp(), now=now)

    assert stale["status"] == "UNKNOWN" and stale["stale"] is True
    assert stale["reason"] == "calendar_cache_stale"
    assert unavailable["status"] == "UNKNOWN" and unavailable["stale"] is True
    assert unavailable["reason"] == "calendar_provider_unavailable"
    assert epoch_cache["source_timestamp"] == "2026-08-25T13:00:00+00:00"


def test_malformed_and_no_event_behaviors_are_safe():
    now = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    malformed = fa.build_fundamental_context(
        [{"title": "CPI m/m", "country": "USD", "impact": "High", "dt": "not-a-time"}],
        fetched_at=now, now=now,
    )
    ordinary = fa.build_fundamental_context(
        [_event("Retail Sales m/m", now + timedelta(minutes=5))],
        fetched_at=now, now=now,
    )

    assert malformed["status"] == "UNKNOWN"
    assert malformed["reason"] == "relevant_event_timestamp_malformed"
    assert ordinary["status"] == "NEUTRAL"
    assert ordinary["event_phase"] == "NONE"


def test_scope_covers_only_required_high_impact_us_events():
    now = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    event = fa.build_fundamental_context(
        [
            _event("ECB President Speaks", now + timedelta(minutes=2), country="EUR"),
            _event("PCE Price Index m/m", now + timedelta(minutes=8)),
        ],
        fetched_at=now, now=now,
    )
    assert event["status"] == "EVENT_RISK"
    assert event["event_name"] == "PCE Price Index m/m"
    assert event["source"] == "BEA"


def test_component_has_no_execution_or_risk_calls_and_is_pure():
    source = inspect.getsource(fa)
    forbidden = ("execute_trade_gateway", "send_to_traderspost", "prop_firm",
                 "risk_", "requests.", "import app")
    assert not any(token in source for token in forbidden)

    now = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    events = [_event("CPI m/m", now + timedelta(minutes=15))]
    first = fa.build_fundamental_context(events, fetched_at=now, now=now)
    second = fa.build_fundamental_context(events, fetched_at=now, now=now)
    assert first == second


def test_shadow_attachment_preserves_technical_verdict_and_never_calls_execution_or_risk():
    import app

    technical = {
        "verdict": "LONG READY",
        "edge_score": 85,
        "strict_reason": "All technical confirmations passed.",
        "trade_plan": {"entry": 100.0, "stop_loss": 99.0, "target1": 101.0},
        "risk_label": "LOW",
    }
    observed = {
        "status": "EVENT_RISK", "shadow_only": True, "event_name": "CPI m/m",
        "impact": "HIGH", "scheduled_at": "2026-08-25T13:15:00+00:00",
        "minutes_to_event": 15, "event_phase": "BEFORE", "source": "BLS",
        "source_timestamp": "2026-08-25T13:00:00+00:00", "stale": False,
        "reason": "scheduled_high_impact_event_nearby",
    }
    before = dict(technical)
    with (
        patch.object(app, "FUNDAMENTAL_AWARENESS_ENABLED", True),
        patch.object(app, "FUNDAMENTAL_AWARENESS_SHADOW_ENABLED", True),
        patch.object(app, "get_fundamental_context", return_value=observed),
        patch.object(app, "execute_trade_gateway", side_effect=AssertionError("execution called")),
        patch.object(app, "prop_firm_status_view", side_effect=AssertionError("risk called")),
    ):
        returned = app._attach_fundamental_context_shadow(technical, "MNQ")

    assert returned is technical
    assert {key: technical[key] for key in before} == before
    assert technical["fundamental_context"] == observed


def test_master_and_shadow_flags_are_both_required_for_legacy_identity():
    import app

    technical = {"verdict": "WAIT", "edge_score": 42, "risk_label": "MEDIUM"}
    before = dict(technical)
    with (
        patch.object(app, "FUNDAMENTAL_AWARENESS_ENABLED", True),
        patch.object(app, "FUNDAMENTAL_AWARENESS_SHADOW_ENABLED", False),
        patch.object(app, "get_fundamental_context", side_effect=AssertionError("unexpected call")),
    ):
        app._attach_fundamental_context_shadow(technical, "MNQ")

    assert technical == before
    assert "fundamental_context" not in technical