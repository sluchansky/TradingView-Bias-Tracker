"""Central Ghost Coordinator — Phase 1 shadow intake only.

This module creates a canonical identity for research observations so existing
ghost/research producers can be compared without changing their writes,
watchers, outcomes, scores, promotion, or execution behavior.

It deliberately imports neither ``app`` nor any execution, broker, gateway, or
database module.  Phase 1 keeps the report process-local and observational; a
future phase may add explicit additive persistence after the comparison data has
been reviewed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


VALID_DIRECTIONS = frozenset(("Long", "Short"))


def _utc_text(value: Any) -> str:
    """Return a stable UTC timestamp string without accepting an implicit clock."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError("signal_time is required")
    return text


def _json_scalar(value: Any) -> Any:
    """Reduce context values to JSON-safe immutable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, Mapping):
        return {str(k): _json_scalar(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_scalar(v) for v in value]
    return str(value)


def _stable_hash(prefix: str, payload: Mapping[str, Any]) -> str:
    packed = json.dumps(_json_scalar(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "%s_%s" % (prefix, hashlib.sha256(packed.encode("utf-8")).hexdigest()[:24])


@dataclass(frozen=True)
class ObservationRequest:
    """Immutable Phase-1 request from one existing research producer."""

    source_system: str
    source_event_id: str
    instrument: str
    timeframe: str
    setup_family: str
    strategy_name: str
    strategy_version: str
    direction: str
    signal_time: Any
    source_bar_time: Any
    entry: Any
    stop: Any
    targets: Sequence[Any]
    context: Mapping[str, Any] = field(default_factory=dict)
    experiment_variant: Optional[str] = None


@dataclass(frozen=True)
class SubmissionResult:
    accepted: bool
    ignored: bool
    market_opportunity_id: Optional[str] = None
    observation_id: Optional[str] = None
    duplicate: bool = False
    error: Optional[str] = None


class CentralGhostCoordinator:
    """Thread-safe, fail-open, in-memory canonical observation registry."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._persistence_enabled = False
        self._persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None
        self._lock = threading.Lock()
        self._requests_received = 0
        self._duplicates = 0
        self._rejected = 0
        self._errors = 0
        self._source_counts: Counter[str] = Counter()
        self._source_unique: Dict[str, set[str]] = defaultdict(set)
        self._source_errors: Counter[str] = Counter()
        self._telemetry_events: set[str] = set()
        self._opportunities: Dict[str, Dict[str, Any]] = {}
        self._observations: Dict[str, Dict[str, Any]] = {}
        self._last_error: Optional[str] = None
        self._persistence_writes = 0
        self._persistence_errors = 0
        self._restored_observations = 0

    def configure(self, *, enabled: bool, persistence_enabled: bool = False,
                  persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None) -> None:
        """Enable or disable shadow intake without clearing observed evidence."""
        with self._lock:
            self._enabled = bool(enabled)
            self._persistence_enabled = bool(persistence_enabled)
            self._persist_fn = persist_fn

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def submit(self, request: ObservationRequest) -> SubmissionResult:
        """Accept one shadow request; invalid input is measured and never raises."""
        with self._lock:
            if not self._enabled:
                return SubmissionResult(accepted=False, ignored=True)
            self._requests_received += 1
        try:
            normalized = self._normalize(request)
            market_id = _stable_hash("mop", {
                "instrument": normalized["instrument"],
                "timeframe": normalized["timeframe"],
                "source_event_id": normalized["source_event_id"],
                "source_bar_time": normalized["source_bar_time"],
                "direction": normalized["direction"],
                "setup_family": normalized["setup_family"],
                "strategy_version": normalized["strategy_version"],
            })
            observation_id = _stable_hash("obs", {
                "market_opportunity_id": market_id,
                "source_system": normalized["source_system"],
                "strategy_name": normalized["strategy_name"],
                "experiment_variant": normalized["experiment_variant"],
            })
            with self._lock:
                self._source_counts[normalized["source_system"]] += 1
                existing = self._observations.get(observation_id)
                if existing is not None:
                    self._duplicates += 1
                    return SubmissionResult(
                        accepted=True,
                        ignored=False,
                        market_opportunity_id=market_id,
                        observation_id=observation_id,
                        duplicate=True,
                    )
                opportunity = self._opportunities.get(market_id)
                if opportunity is None:
                    opportunity = {
                        "market_opportunity_id": market_id,
                        "instrument": normalized["instrument"],
                        "timeframe": normalized["timeframe"],
                        "source_event_id": normalized["source_event_id"],
                        "source_bar_time": normalized["source_bar_time"],
                        "direction": normalized["direction"],
                        "setup_family": normalized["setup_family"],
                        "strategy_version": normalized["strategy_version"],
                        "source_systems": set(),
                        "observation_ids": [],
                    }
                    self._opportunities[market_id] = opportunity
                opportunity["source_systems"].add(normalized["source_system"])
                opportunity["observation_ids"].append(observation_id)
                stored = {
                    **normalized,
                    "market_opportunity_id": market_id,
                    "observation_id": observation_id,
                }
                self._observations[observation_id] = stored
                self._source_unique[normalized["source_system"]].add(market_id)
            self._persist("observation", stored)
            return SubmissionResult(
                accepted=True,
                ignored=False,
                market_opportunity_id=market_id,
                observation_id=observation_id,
            )
        except Exception as exc:
            with self._lock:
                self._rejected += 1
                source = str(getattr(request, "source_system", "") or "UNKNOWN").strip() or "UNKNOWN"
                self._source_errors[source] += 1
                self._last_error = str(exc)[:180]
            return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180])

    def report(self, limit: int = 100) -> Dict[str, Any]:
        """Return a JSON-safe read-only comparison report."""
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            source_systems = sorted(set(self._source_counts) | set(self._source_unique) | set(self._source_errors))
            rows = [{
                "source_system": source,
                "submissions": int(self._source_counts[source]),
                "unique_opportunities": len(self._source_unique[source]),
            } for source in source_systems]
            cross = []
            for opportunity in self._opportunities.values():
                sources = sorted(opportunity["source_systems"])
                if len(sources) > 1:
                    cross.append({
                        "market_opportunity_id": opportunity["market_opportunity_id"],
                        "instrument": opportunity["instrument"],
                        "timeframe": opportunity["timeframe"],
                        "source_event_id": opportunity["source_event_id"],
                        "direction": opportunity["direction"],
                        "setup_family": opportunity["setup_family"],
                        "source_systems": sources,
                        "legacy_representations": len(opportunity["observation_ids"]),
                    })
            cross.sort(key=lambda item: (-item["legacy_representations"], item["market_opportunity_id"]))
            for row in rows:
                row["duplicates"] = 0
                row["cross_system_matches"] = sum(
                    1 for item in cross if row["source_system"] in item["source_systems"]
                )
                row["errors"] = int(self._source_errors[row["source_system"]])
            return {
                "ok": True,
                "phase": 1,
                "enabled": self._enabled,
                "persistence": ("postgres_shadow_only" if self._persistence_enabled
                                else "in_memory_shadow_only"),
                "persistence_enabled": self._persistence_enabled,
                "persistence_writes": self._persistence_writes,
                "persistence_errors": self._persistence_errors,
                "restored_observations": self._restored_observations,
                "requests_received": self._requests_received,
                "unique_market_opportunities": len(self._opportunities),
                "unique_observations": len(self._observations),
                "visual_or_nontrade_events": len(self._telemetry_events),
                "duplicate_submissions": self._duplicates,
                "malformed_or_rejected": self._rejected,
                "coordinator_errors": self._errors,
                "last_error": self._last_error,
                "source_systems": rows,
                "cross_system_match_count": len(cross),
                "cross_system_opportunities": cross[:safe_limit],
            }

    @staticmethod
    def _normalize(request: ObservationRequest) -> Dict[str, Any]:
        if not isinstance(request, ObservationRequest):
            raise ValueError("request must be an ObservationRequest")
        source_system = str(request.source_system or "").strip()
        source_event_id = str(request.source_event_id or "").strip()
        instrument = str(request.instrument or "").strip().upper()
        timeframe = str(request.timeframe or "").strip().lower()
        setup_family = str(request.setup_family or "").strip().upper()
        strategy_name = str(request.strategy_name or "").strip()
        strategy_version = str(request.strategy_version or "").strip()
        direction = str(request.direction or "").strip().title()
        if not all((source_system, source_event_id, instrument, timeframe, setup_family, strategy_name, strategy_version)):
            raise ValueError("source, event, instrument, timeframe, family, strategy, and version are required")
        if direction not in VALID_DIRECTIONS:
            raise ValueError("direction must be Long or Short")
        signal_time = _utc_text(request.signal_time)
        source_bar_time = _utc_text(request.source_bar_time)
        try:
            entry = float(request.entry)
            stop = float(request.stop)
            targets = tuple(float(value) for value in request.targets if value is not None)
        except (TypeError, ValueError):
            raise ValueError("entry, stop, and targets must be numeric")
        if entry == stop:
            raise ValueError("entry and stop must differ")
        if not targets:
            raise ValueError("at least one target is required")
        return {
            "source_system": source_system,
            "source_event_id": source_event_id,
            "instrument": instrument,
            "timeframe": timeframe,
            "setup_family": setup_family,
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "direction": direction,
            "signal_time": signal_time,
            "source_bar_time": source_bar_time,
            "entry": entry,
            "stop": stop,
            "targets": targets,
            "context": _json_scalar(request.context or {}),
            "experiment_variant": (str(request.experiment_variant).strip() if request.experiment_variant else None),
        }

    def record_observational_event(self, source_system: str, event_id: str) -> SubmissionResult:
        """Count a non-trade research event without manufacturing trade geometry."""
        with self._lock:
            if not self._enabled:
                return SubmissionResult(accepted=False, ignored=True)
            source = str(source_system or "").strip()
            event = str(event_id or "").strip()
            if not source or not event:
                self._rejected += 1
                return SubmissionResult(accepted=False, ignored=False, error="source_system and event_id are required")
            self._requests_received += 1
            self._source_counts[source] += 1
            telemetry_id = "%s|%s" % (source, event)
            if telemetry_id in self._telemetry_events:
                self._duplicates += 1
                return SubmissionResult(accepted=True, ignored=False, duplicate=True)
            self._telemetry_events.add(telemetry_id)
        self._persist("telemetry", {
            "source_system": source,
            "event_id": event,
            "telemetry_id": telemetry_id,
        })
        return SubmissionResult(accepted=True, ignored=False)

    def restore(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Rehydrate prior shadow evidence without re-writing it or running rules."""
        restored = 0
        for raw in records:
            try:
                stored = dict(raw)
                market_id = str(stored["market_opportunity_id"])
                observation_id = str(stored["observation_id"])
                source = str(stored["source_system"])
                with self._lock:
                    if observation_id in self._observations:
                        continue
                    opportunity = self._opportunities.setdefault(market_id, {
                        "market_opportunity_id": market_id,
                        "instrument": stored["instrument"],
                        "timeframe": stored["timeframe"],
                        "source_event_id": stored["source_event_id"],
                        "source_bar_time": stored["source_bar_time"],
                        "direction": stored["direction"],
                        "setup_family": stored["setup_family"],
                        "strategy_version": stored["strategy_version"],
                        "source_systems": set(),
                        "observation_ids": [],
                    })
                    opportunity["source_systems"].add(source)
                    opportunity["observation_ids"].append(observation_id)
                    self._observations[observation_id] = stored
                    self._source_unique[source].add(market_id)
                    self._source_counts[source] += 1
                    self._restored_observations += 1
                    restored += 1
            except Exception:
                with self._lock:
                    self._persistence_errors += 1
        return restored

    def restore_telemetry(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Restore non-trade telemetry without fabricating market opportunities."""
        restored = 0
        for raw in records:
            try:
                source = str(raw["source_system"])
                event = str(raw["event_id"])
                telemetry_id = str(raw.get("telemetry_id") or "%s|%s" % (source, event))
                with self._lock:
                    if telemetry_id in self._telemetry_events:
                        continue
                    self._telemetry_events.add(telemetry_id)
                    self._source_counts[source] += 1
                    restored += 1
            except Exception:
                with self._lock:
                    self._persistence_errors += 1
        return restored

    def _persist(self, kind: str, record: Mapping[str, Any]) -> None:
        """Best-effort boundary: storage failure is never an intake failure."""
        with self._lock:
            enabled, fn = self._persistence_enabled, self._persist_fn
        if not enabled or fn is None:
            return
        try:
            if fn(kind, record):
                with self._lock:
                    self._persistence_writes += 1
        except Exception:
            with self._lock:
                self._persistence_errors += 1


_DEFAULT_COORDINATOR = CentralGhostCoordinator(enabled=False)


def configure(*, enabled: bool, persistence_enabled: bool = False,
              persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None) -> None:
    """Configure the module-level coordinator used by legacy shadow adapters."""
    _DEFAULT_COORDINATOR.configure(
        enabled=enabled, persistence_enabled=persistence_enabled, persist_fn=persist_fn)


def submit_shadow(request: ObservationRequest) -> SubmissionResult:
    """Fail-open boundary for producer adapters."""
    try:
        return _DEFAULT_COORDINATOR.submit(request)
    except Exception as exc:  # defensive boundary — legacy producers must continue
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180])


def get_report(limit: int = 100) -> Dict[str, Any]:
    """Read-only report for the coordinator diagnostic endpoint."""
    return _DEFAULT_COORDINATOR.report(limit=limit)


def record_observational_event(source_system: str, event_id: str) -> SubmissionResult:
    """Fail-open boundary for non-trade systems such as Visual Brain."""
    try:
        return _DEFAULT_COORDINATOR.record_observational_event(source_system, event_id)
    except Exception as exc:
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180])


def restore(records: Iterable[Mapping[str, Any]]) -> int:
    """Restore persisted shadow observations after the app's no-DDL boot probe."""
    return _DEFAULT_COORDINATOR.restore(records)


def restore_telemetry(records: Iterable[Mapping[str, Any]]) -> int:
    """Restore persisted non-trade coordinator telemetry after the no-DDL probe."""
    return _DEFAULT_COORDINATOR.restore_telemetry(records)