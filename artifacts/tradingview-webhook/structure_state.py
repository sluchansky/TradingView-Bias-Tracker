"""Pure market-structure cycle resolver for BOS / CHOCH evidence.

The producers keep their conventional labels:
  * CHOCH is the first break against the prior trend: a reversal candidate.
  * BOS confirms a same-trend continuation or the pending CHOCH reversal.

One active cycle earns a bounded structure allocation: +20 while its CHOCH is a
candidate, then +40 when a same-direction BOS confirms it. Historical events are
never accumulated across cycles or allowed to survive a newer opposite-side CHOCH.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable


STRUCTURE_CANDIDATE_POINTS = 20
STRUCTURE_POINTS = 40
_STRUCTURE_TYPES = frozenset(
    {"CHOCH DEMAND", "CHOCH SUPPLY", "BOS DEMAND", "BOS SUPPLY"}
)
_INSTRUMENT_RE = re.compile(r"\b(MGC|MNQ|MES|MYM)(?:\d+)?!?\b", re.IGNORECASE)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _instrument_of(event: dict[str, Any]) -> str | None:
    explicit = str(event.get("instrument") or "").upper().strip()
    if explicit:
        match = _INSTRUMENT_RE.search(explicit)
        return match.group(1).upper() if match else explicit
    for field in ("ticker", "alert_type"):
        match = _INSTRUMENT_RE.search(str(event.get(field) or ""))
        if match:
            return match.group(1).upper()
    return None


def _neutral_state(instrument: str, window_minutes: int) -> dict[str, Any]:
    return {
        "instrument": instrument,
        "state": "NO_STRUCTURE",
        "direction": None,
        "confirmed": False,
        "allocation_points": 0,
        "active_event": None,
        "last_event": None,
        "cycle_started_at": None,
        "last_event_at": None,
        "next_event": "CHOCH DEMAND or CHOCH SUPPLY",
        "next_event_reason": "Wait for the first counter-trend break to establish a reversal candidate.",
        "events_in_cycle": 0,
        "superseded_events": 0,
        "window_minutes": window_minutes,
        "summary": "No active BOS/CHOCH structure cycle.",
    }


def resolve_structure_cycle(
    alert_history: Iterable[dict[str, Any]],
    instrument: str,
    *,
    now: datetime | None = None,
    window_minutes: int = 20,
) -> dict[str, Any]:
    """Resolve one instrument's active BOS/CHOCH cycle.

    The result is intentionally JSON-safe and contains the exact current state,
    the one score allocation (0, 20, or 40), and the next structurally valid event.
    It only reads the supplied history; callers own all storage and execution.
    """
    inst = str(instrument or "").upper()
    result = _neutral_state(inst, int(window_minutes))
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    cutoff = current - timedelta(minutes=max(0, int(window_minutes)))

    events: list[tuple[datetime, int, str]] = []
    for index, raw in enumerate(alert_history or ()):
        if not isinstance(raw, dict) or raw.get("canonical") is False:
            continue
        if _instrument_of(raw) != inst:
            continue
        event_type = str(raw.get("alert_type") or "").upper().strip()
        if event_type not in _STRUCTURE_TYPES:
            continue
        event_at = _as_utc(raw.get("timestamp"))
        if event_at is None or event_at < cutoff or event_at > current + timedelta(minutes=1):
            continue
        events.append((event_at, index, event_type))
    events.sort(key=lambda item: (item[0], item[1]))

    state = result["state"]
    direction: str | None = None
    cycle_started_at: datetime | None = None
    last_event_at: datetime | None = None
    last_event: str | None = None
    active_event: str | None = None
    events_in_cycle = 0
    superseded_events = 0

    for event_at, _index, event_type in events:
        event_direction = "Long" if event_type.endswith("DEMAND") else "Short"
        is_choch = event_type.startswith("CHOCH ")

        if is_choch:
            if state == "REVERSAL_CANDIDATE" and direction == event_direction:
                # A duplicate candidate must not restart a cycle's clock or become
                # a second source of credit.
                continue
            if state != "NO_STRUCTURE":
                superseded_events += events_in_cycle
            state = "REVERSAL_CANDIDATE"
            direction = event_direction
            cycle_started_at = event_at
            last_event_at = event_at
            active_event = last_event = event_type
            events_in_cycle = 1
            continue

        # BOS can establish a continuation from a neutral tape, confirm the active
        # same-direction CHOCH, or extend an existing confirmed trend.  A BOS in the
        # opposite direction without its own CHOCH is invalid sequence noise.
        if state == "NO_STRUCTURE":
            state = "TREND_CONFIRMED"
            direction = event_direction
            cycle_started_at = event_at
            last_event_at = event_at
            active_event = last_event = event_type
            events_in_cycle = 1
        elif direction == event_direction:
            state = "REVERSAL_CONFIRMED" if state == "REVERSAL_CANDIDATE" else "TREND_CONFIRMED"
            last_event_at = event_at
            last_event = event_type
            events_in_cycle += 1

    if state == "NO_STRUCTURE":
        return result

    confirmed = state in {"TREND_CONFIRMED", "REVERSAL_CONFIRMED"}
    suffix = "DEMAND" if direction == "Long" else "SUPPLY"
    opposite = "SUPPLY" if direction == "Long" else "DEMAND"
    if confirmed:
        next_event = f"CHOCH {opposite}"
        next_reason = (
            f"Current {direction.lower()} structure is confirmed. "
            f"The next valid state change is {next_event}, a new reversal candidate."
        )
        summary = (
            f"{direction} {state.replace('_', ' ').lower()} — "
            f"one {STRUCTURE_POINTS}-point structure allocation is active."
        )
    else:
        next_event = f"BOS {suffix}"
        next_reason = (
            f"{direction} CHOCH is a reversal candidate only. "
            f"Wait for {next_event} to confirm the new structure cycle."
        )
        summary = f"{direction} reversal candidate — awaiting {next_event}."

    return {
        "instrument": inst,
        "state": state,
        "direction": direction,
        "confirmed": confirmed,
        "allocation_points": (
            STRUCTURE_POINTS if confirmed else STRUCTURE_CANDIDATE_POINTS
        ),
        "active_event": active_event,
        "last_event": last_event,
        "cycle_started_at": cycle_started_at.isoformat() if cycle_started_at else None,
        "last_event_at": last_event_at.isoformat() if last_event_at else None,
        "next_event": next_event,
        "next_event_reason": next_reason,
        "events_in_cycle": events_in_cycle,
        "superseded_events": superseded_events,
        "window_minutes": window_minutes,
        "summary": summary,
    }