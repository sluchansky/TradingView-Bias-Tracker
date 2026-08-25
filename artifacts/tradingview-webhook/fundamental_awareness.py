"""Phase 1 scheduled-event awareness.

This module is intentionally a pure, read-only projection.  It accepts an
already-cached economic-calendar snapshot and returns one normalized context
object.  It does not import the application, make network calls, inspect
trading state, or expose any execution/risk hooks.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
EVENT_BEFORE_MIN = 30
EVENT_ACTIVE_MIN = 5
EVENT_AFTER_MIN = 15

_EVENT_RULES = (
    # More specific FOMC conference wording must win over generic Fed wording.
    (("fomc press conference", "fed chair press conference", "powell press conference"),
     "FOMC press conference", "Federal Reserve"),
    (("fomc statement", "fomc rate decision", "fomc decision",
      "federal funds rate", "fed interest rate decision"),
     "FOMC decision", "Federal Reserve"),
    (("employment situation", "nonfarm payroll", "non-farm payroll",
      "nonfarm employment", "nfp"),
     "Employment Situation/NFP", "BLS"),
    (("consumer price index", "cpi"),
     "CPI", "BLS"),
    (("producer price index", "ppi"),
     "PPI", "BLS"),
    (("personal consumption expenditures", "pce price", "core pce", "pce"),
     "PCE", "BEA"),
    (("gross domestic product", "gdp"),
     "GDP", "BEA"),
    (("fed chair speech", "fed chair speaks", "powell speech",
      "powell speaks", "federal reserve chair speech"),
     "Scheduled Fed Chair speech", "Federal Reserve"),
)


def _neutral(status="NEUTRAL", *, stale=False, source=None,
             source_timestamp=None, reason="no_relevant_event_in_window"):
    return {
        "status": status,
        "shadow_only": True,
        "event_name": None,
        "impact": None,
        "scheduled_at": None,
        "minutes_to_event": None,
        "event_phase": "NONE",
        "source": source,
        "source_timestamp": source_timestamp,
        "stale": bool(stale),
        "reason": reason,
    }


def _iso_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _as_utc(value):
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify_scheduled_event(title):
    """Return ``(event_name, source)`` for an in-scope event title."""
    text = str(title or "").strip().lower()
    if not text:
        return None
    for keywords, event_name, source in _EVENT_RULES:
        if any(keyword in text for keyword in keywords):
            return event_name, source
    return None


def _event_phase(minutes):
    if minutes > 0 and minutes <= EVENT_BEFORE_MIN:
        return "BEFORE"
    if -EVENT_ACTIVE_MIN <= minutes <= 0:
        return "ACTIVE"
    if minutes < -EVENT_ACTIVE_MIN and minutes >= -EVENT_AFTER_MIN:
        return "AFTER"
    return "NONE"


def build_fundamental_context(events, fetched_at=None, now=None,
                              stale=False, provider_error=None):
    """Normalize a cached calendar list into the Phase 1 contract.

    ``events`` must already be cached data.  No call in this function can fetch
    data or invoke an evaluator.  Naive event timestamps follow the existing
    calendar contract and are treated as UTC; aware timestamps are converted
    through America/New_York for the window calculation, preserving DST.
    """
    source_timestamp = _iso_timestamp(fetched_at)
    if provider_error or events is None:
        return _neutral(
            "UNKNOWN",
            stale=True,
            source=None,
            source_timestamp=source_timestamp,
            reason="calendar_provider_unavailable",
        )
    if stale:
        return _neutral(
            "UNKNOWN",
            stale=True,
            source="cached economic calendar",
            source_timestamp=source_timestamp,
            reason="calendar_cache_stale",
        )
    if not isinstance(events, (list, tuple)):
        return _neutral(
            "UNKNOWN",
            stale=True,
            source=None,
            source_timestamp=source_timestamp,
            reason="calendar_payload_malformed",
        )

    now_utc = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    candidates = []
    malformed_relevant = False
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("impact") or "").strip().lower() != "high":
            continue
        country = str(event.get("country") or event.get("currency") or "").strip().upper()
        if country and country != "USD":
            continue
        classified = classify_scheduled_event(event.get("title") or event.get("name"))
        if not classified:
            continue
        event_dt = _as_utc(event.get("dt") or event.get("scheduled_at")
                            or event.get("date") or event.get("timestamp"))
        if event_dt is None:
            malformed_relevant = True
            continue
        minutes = (event_dt.astimezone(ET) - now_utc.astimezone(ET)).total_seconds() / 60.0
        phase = _event_phase(minutes)
        if phase == "NONE":
            continue
        event_name, default_source = classified
        candidates.append({
            "event_name": str(event.get("title") or event_name).strip() or event_name,
            "impact": "HIGH",
            "scheduled_at": event_dt.isoformat(),
            "minutes_to_event": int(round(minutes)),
            "event_phase": phase,
            "source": str(event.get("source") or default_source),
            "source_timestamp": source_timestamp,
            "stale": False,
        })

    if not candidates:
        return _neutral(
            "UNKNOWN" if malformed_relevant else "NEUTRAL",
            stale=bool(malformed_relevant),
            source="cached economic calendar" if not malformed_relevant else None,
            source_timestamp=source_timestamp,
            reason=("relevant_event_timestamp_malformed"
                    if malformed_relevant else "no_relevant_event_in_window"),
        )

    selected = min(
        candidates,
        key=lambda item: (abs(item["minutes_to_event"]), item["minutes_to_event"]),
    )
    return {
        "status": "EVENT_RISK",
        "shadow_only": True,
        **selected,
        "reason": "scheduled_high_impact_event_nearby",
    }