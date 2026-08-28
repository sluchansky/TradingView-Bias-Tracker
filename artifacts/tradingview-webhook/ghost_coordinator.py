"""Central Ghost Coordinator — research-only canonical intake and fan-out.

This module creates a canonical identity for research observations so existing
ghost/research producers can be compared without changing their writes,
watchers, outcomes, scores, promotion, or execution behavior.

It deliberately imports neither ``app`` nor any execution, broker, gateway, or
database module.  Callers inject research-only delivery callbacks; the
coordinator can never discover or invoke a trading/execution path by itself.
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
    heartbeat: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class DeliveryResult:
    """Result of one research-only fan-out attempt."""

    destination: str
    delivered: bool
    duplicate: bool = False
    error: Optional[str] = None


class CentralGhostCoordinator:
    """Thread-safe, fail-open, in-memory canonical observation registry."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._persistence_enabled = False
        self._persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None
        self._health_aggregate_fn: Optional[Callable[[], Mapping[str, Any]]] = None
        self._lock = threading.Lock()
        self._requests_received = 0
        self._duplicates = 0
        self._evaluation_checks = 0
        self._evaluation_heartbeats = 0
        self._evaluation_transitions = 0
        self._rejected = 0
        self._errors = 0
        self._source_counts: Counter[str] = Counter()
        self._source_unique: Dict[str, set[str]] = defaultdict(set)
        self._source_evaluation_checks: Counter[str] = Counter()
        self._source_evaluation_heartbeats: Counter[str] = Counter()
        self._source_evaluation_transitions: Counter[str] = Counter()
        self._source_errors: Counter[str] = Counter()
        self._telemetry_events: set[str] = set()
        self._opportunities: Dict[str, Dict[str, Any]] = {}
        self._observations: Dict[str, Dict[str, Any]] = {}
        self._evaluation_latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._last_error: Optional[str] = None
        self._persistence_writes = 0
        self._persistence_errors = 0
        self._restored_observations = 0
        self._deliveries: Dict[str, Callable[[Mapping[str, Any]], Any]] = {}
        self._delivery_sources: Dict[str, Optional[frozenset[str]]] = {}
        self._delivered: set[Tuple[str, str]] = set()
        self._delivery_attempts: Counter[str] = Counter()
        self._delivery_successes: Counter[str] = Counter()
        self._delivery_duplicates: Counter[str] = Counter()
        self._delivery_errors: Counter[str] = Counter()
        self._delivery_last_error: Dict[str, str] = {}

    def configure(self, *, enabled: bool, persistence_enabled: Optional[bool] = None,
                  persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None,
                  health_aggregate_fn: Optional[Callable[[], Mapping[str, Any]]] = None) -> None:
        """Enable shadow intake without clearing its optional storage boundary.

        Callers that omit ``persistence_enabled`` are changing intake only. This
        matters when an application module is imported a second time: an
        incidental ``configure(enabled=...)`` must not silently detach a
        coordinator persistence callback already installed at boot.
        """
        with self._lock:
            self._enabled = bool(enabled)
            if persistence_enabled is not None:
                self._persistence_enabled = bool(persistence_enabled)
                self._persist_fn = persist_fn
            elif persist_fn is not None:
                self._persist_fn = persist_fn
            if health_aggregate_fn is not None:
                self._health_aggregate_fn = health_aggregate_fn

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
            # Producers may declare the exact generic-ghost identity they are
            # comparing against.  The durable source_event_id is intentionally
            # left untouched: this only gives an explicitly linked reference the
            # same *market* identity as its generic authority.  No time, price,
            # strategy-name, or current-mode inference is permitted here.
            context = normalized.get("context") or {}
            anchor_id = str(context.get("canonical_authority_id") or "").strip()
            legacy_sim_key = str(context.get("legacy_sim_key") or "").strip()
            evaluation_kind = str(context.get("coordinator_evaluation_kind") or "").strip().lower()
            evaluation_key = str(
                context.get("coordinator_opportunity_key") or ""
            ).strip()
            evaluation_fingerprint = str(
                context.get("coordinator_evaluation_fingerprint") or ""
            ).strip()
            is_evaluation = evaluation_kind == "gate_check" and bool(evaluation_key) and bool(
                evaluation_fingerprint
            )
            # This bridge exists exclusively for the dual-mode simulator.  Its
            # own immutable sim_key must remain the submitted source event and
            # match the carried durable reference.  All other producers,
            # including generic_ghost, continue to use their original IDs.
            use_anchor = (
                normalized["source_system"] == "dual_mode_sim"
                and bool(anchor_id)
                and legacy_sim_key == normalized["source_event_id"]
            )
            # Gate-effectiveness polls have a stable daily setup key but a
            # changing time-bucketed audit id. Keep the opportunity stable and
            # make the meaningful state fingerprint part of the observation
            # identity. This collapses unchanged checks without collapsing a
            # real gate-state transition.
            market_event_id = (
                evaluation_key
                if is_evaluation and normalized["source_system"] == "gate_effectiveness"
                else (anchor_id if use_anchor else normalized["source_event_id"])
            )
            market_id = _stable_hash("mop", {
                "instrument": normalized["instrument"],
                "timeframe": normalized["timeframe"],
                "source_event_id": market_event_id,
                # A gate check's daily setup key is its opportunity identity;
                # the bar that happened to trigger a poll is heartbeat
                # metadata, not a new opportunity.
                "source_bar_time": (
                    None if is_evaluation else normalized["source_bar_time"]
                ),
                "direction": normalized["direction"],
                "setup_family": normalized["setup_family"],
                "strategy_version": normalized["strategy_version"],
            })
            with self._lock:
                self._source_counts[normalized["source_system"]] += 1
                if is_evaluation:
                    source = normalized["source_system"]
                    self._evaluation_checks += 1
                    self._source_evaluation_checks[source] += 1
                    evaluation_state_key = (market_id, source)
                    previous_state = self._evaluation_latest.get(evaluation_state_key)
                    if previous_state is None:
                        transition_index = 0
                    elif previous_state["fingerprint"] == evaluation_fingerprint:
                        transition_index = int(previous_state["transition_index"])
                    else:
                        transition_index = int(previous_state["transition_index"]) + 1
                    observation_id = _stable_hash("obs", {
                        "market_opportunity_id": market_id,
                        "source_system": normalized["source_system"],
                        "strategy_name": normalized["strategy_name"],
                        "experiment_variant": normalized["experiment_variant"],
                        "evaluation_fingerprint": evaluation_fingerprint,
                        "evaluation_transition_index": transition_index,
                    })
                else:
                    transition_index = None
                    observation_id = _stable_hash("obs", {
                        "market_opportunity_id": market_id,
                        "source_system": normalized["source_system"],
                        "strategy_name": normalized["strategy_name"],
                        "experiment_variant": normalized["experiment_variant"],
                        "evaluation_fingerprint": None,
                    })
                existing = self._observations.get(observation_id)
                if existing is not None:
                    self._duplicates += 1
                    if is_evaluation:
                        self._evaluation_heartbeats += 1
                        self._source_evaluation_heartbeats[
                            normalized["source_system"]
                        ] += 1
                        existing_context = existing.setdefault("context", {})
                        try:
                            heartbeat_count = int(
                                existing_context.get("evaluation_heartbeat_count", 0) or 0
                            )
                        except (TypeError, ValueError):
                            heartbeat_count = 0
                        existing_context["evaluation_heartbeat_count"] = heartbeat_count + 1
                        existing_context["evaluation_last_seen_at"] = normalized["signal_time"]
                        existing_context["evaluation_last_source_event_id"] = normalized[
                            "source_event_id"
                        ]
                        heartbeat_record = {
                            "observation_id": observation_id,
                            "market_opportunity_id": market_id,
                            "source_system": normalized["source_system"],
                            "evaluation_heartbeat_count": heartbeat_count + 1,
                            "evaluation_last_seen_at": normalized["signal_time"],
                            "evaluation_last_source_event_id": normalized["source_event_id"],
                            "context": existing_context,
                        }
                    else:
                        heartbeat_record = None
                else:
                    if is_evaluation:
                        self._evaluation_transitions += 1
                        self._source_evaluation_transitions[
                            normalized["source_system"]
                        ] += 1
                        normalized["context"]["evaluation_transition_index"] = transition_index
                        self._evaluation_latest[evaluation_state_key] = {
                            "fingerprint": evaluation_fingerprint,
                            "transition_index": transition_index,
                            "observation_id": observation_id,
                        }
                    heartbeat_record = None
                if existing is None:
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
            if heartbeat_record is not None:
                self._persist("evaluation_heartbeat", heartbeat_record)
                return SubmissionResult(
                    accepted=True,
                    ignored=False,
                    market_opportunity_id=market_id,
                    observation_id=observation_id,
                    duplicate=True,
                    heartbeat=True,
                )
            if existing is not None:
                return SubmissionResult(
                    accepted=True,
                    ignored=False,
                    market_opportunity_id=market_id,
                    observation_id=observation_id,
                    duplicate=True,
                )
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

    def register_delivery(self, destination: str,
                          callback: Callable[[Mapping[str, Any]], Any],
                          *, sources: Optional[Sequence[str]] = None) -> None:
        """Register one injected research delivery callback.

        Destination callbacks are intentionally generic data callbacks. They run
        outside the coordinator lock and must be registered by the hosting
        research application; this module never imports an execution subsystem.
        Re-registering a destination deliberately replaces its callback without
        clearing prior delivery idempotency.
        """
        name = str(destination or "").strip()
        if not name:
            raise ValueError("destination is required")
        if not callable(callback):
            raise ValueError("delivery callback must be callable")
        source_filter = None
        if sources is not None:
            source_filter = frozenset(str(item).strip() for item in sources if str(item).strip())
        with self._lock:
            self._deliveries[name] = callback
            self._delivery_sources[name] = source_filter

    def unregister_delivery(self, destination: str) -> None:
        """Remove a callback; delivery history remains for report consistency."""
        name = str(destination or "").strip()
        with self._lock:
            self._deliveries.pop(name, None)
            self._delivery_sources.pop(name, None)

    def route(self, request: ObservationRequest) -> Tuple[SubmissionResult, Tuple[DeliveryResult, ...]]:
        """Canonical intake followed by best-effort research-only fan-out.

        A duplicate or rejected intake is never delivered. Each destination sees
        an isolated JSON-safe copy and a failure in one destination never blocks
        another destination or changes the accepted intake result.
        """
        result = self.submit(request)
        if not result.accepted or result.duplicate or not result.observation_id:
            return result, ()
        with self._lock:
            record = self._observations.get(result.observation_id)
            callbacks = [
                (name, callback)
                for name, callback in self._deliveries.items()
                if self._delivery_sources.get(name) is None
                or record.get("source_system") in self._delivery_sources[name]
            ] if record else []
        return result, tuple(self._deliver_one(name, callback, record) for name, callback in callbacks)

    def _deliver_one(self, destination: str, callback: Callable[[Mapping[str, Any]], Any],
                     record: Optional[Mapping[str, Any]]) -> DeliveryResult:
        if not record:
            return DeliveryResult(destination=destination, delivered=False, error="missing observation")
        record_id = str(record.get("observation_id") or record.get("telemetry_id") or "")
        if not record_id:
            return DeliveryResult(destination=destination, delivered=False, error="missing delivery identity")
        key = (destination, record_id)
        with self._lock:
            self._delivery_attempts[destination] += 1
            if key in self._delivered:
                self._delivery_duplicates[destination] += 1
                return DeliveryResult(destination=destination, delivered=False, duplicate=True)
            # Reserve before invoking the callback to prevent concurrent duplicate
            # fan-out. A failure removes its reservation so a future explicit
            # re-route can try again; retries are never scheduled implicitly.
            self._delivered.add(key)
        payload = _json_scalar(dict(record))
        try:
            callback(payload)
            with self._lock:
                self._delivery_successes[destination] += 1
            return DeliveryResult(destination=destination, delivered=True)
        except Exception as exc:
            message = str(exc)[:180]
            with self._lock:
                self._delivered.discard(key)
                self._delivery_errors[destination] += 1
                self._delivery_last_error[destination] = message
            return DeliveryResult(destination=destination, delivered=False, error=message)

    def report(self, limit: int = 100) -> Dict[str, Any]:
        """Return a JSON-safe read-only comparison report."""
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            health_aggregate_fn = self._health_aggregate_fn
        durable_totals: Dict[str, Any] = {}
        if health_aggregate_fn is not None:
            try:
                candidate = health_aggregate_fn()
                if isinstance(candidate, Mapping):
                    durable_totals = dict(candidate)
            except Exception as exc:
                durable_totals = {
                    "db_ready": False,
                    "complete": False,
                    "error": str(exc)[:180],
                }
        with self._lock:
            source_systems = sorted(set(self._source_counts) | set(self._source_unique) | set(self._source_errors))
            rows = [{
                "source_system": source,
                "submissions": int(self._source_counts[source]),
                "unique_opportunities": len(self._source_unique[source]),
                "evaluation_checks": int(self._source_evaluation_checks[source]),
                "evaluation_heartbeats": int(self._source_evaluation_heartbeats[source]),
                "evaluation_transitions": int(self._source_evaluation_transitions[source]),
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
                "phase": 2,
                "routing_mode": ("research_fanout" if self._deliveries else "shadow_intake"),
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
                "opportunity_count": len(self._opportunities),
                "opportunity_observation_count": len(self._observations),
                "evaluation_checks": self._evaluation_checks,
                "evaluation_heartbeats": self._evaluation_heartbeats,
                "evaluation_transitions": self._evaluation_transitions,
                "visual_or_nontrade_events": len(self._telemetry_events),
                "duplicate_submissions": self._duplicates,
                "malformed_or_rejected": self._rejected,
                "coordinator_errors": self._errors,
                "last_error": self._last_error,
                "source_systems": rows,
                "cross_system_match_count": len(cross),
                "cross_system_opportunities": cross[:safe_limit],
                "delivery_attempts": int(sum(self._delivery_attempts.values())),
                "delivery_successes": int(sum(self._delivery_successes.values())),
                "delivery_failures": int(sum(self._delivery_errors.values())),
                "delivery_destinations": [{
                    "destination": destination,
                    "source_filter": sorted(self._delivery_sources.get(destination) or ()),
                    "attempted": int(self._delivery_attempts[destination]),
                    "delivered": int(self._delivery_successes[destination]),
                    "duplicates": int(self._delivery_duplicates[destination]),
                    "errors": int(self._delivery_errors[destination]),
                    "last_error": self._delivery_last_error.get(destination),
                } for destination in sorted(set(self._deliveries) | set(self._delivery_attempts)
                                              | set(self._delivery_errors))],
                # The in-memory values above intentionally remain session-scoped:
                # they are the exact deduplication window restored at boot.  A
                # host may provide a SELECT-only aggregate over the full durable
                # store for complete health totals without loading all rows.
                "restored_session_counts": {
                    "opportunity_count": len(self._opportunities),
                    "observation_count": len(self._observations),
                    "evaluation_checks": self._evaluation_checks,
                    "evaluation_heartbeats": self._evaluation_heartbeats,
                    "evaluation_transitions": self._evaluation_transitions,
                    "telemetry_event_count": len(self._telemetry_events),
                    "restored_observations": self._restored_observations,
                },
                "durable_totals": durable_totals,
                "health_totals": {
                    "source": (
                        "durable"
                        if durable_totals.get("db_ready") and durable_totals.get("complete")
                        else "restored_session"
                    ),
                    "complete": bool(
                        durable_totals.get("db_ready") and durable_totals.get("complete")
                    ),
                    "opportunity_count": int(
                        durable_totals.get("opportunity_count", len(self._opportunities))
                        or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else len(self._opportunities),
                    "observation_count": int(
                        durable_totals.get("observation_count", len(self._observations))
                        or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else len(self._observations),
                    "evaluation_checks": int(
                        durable_totals.get("evaluation_checks", self._evaluation_checks)
                        or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else self._evaluation_checks,
                    "evaluation_heartbeats": int(
                        durable_totals.get(
                            "evaluation_heartbeats", self._evaluation_heartbeats
                        ) or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else self._evaluation_heartbeats,
                    "evaluation_transitions": int(
                        durable_totals.get(
                            "evaluation_transitions", self._evaluation_transitions
                        ) or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else self._evaluation_transitions,
                    "telemetry_event_count": int(
                        durable_totals.get(
                            "telemetry_event_count", len(self._telemetry_events)
                        ) or 0
                    ) if durable_totals.get("db_ready") and durable_totals.get("complete")
                    else len(self._telemetry_events),
                },
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

    def route_observational_event(self, source_system: str, event_id: str) -> Tuple[SubmissionResult, Tuple[DeliveryResult, ...]]:
        """Record non-trade telemetry and fan it out without inventing geometry."""
        result = self.record_observational_event(source_system, event_id)
        if not result.accepted or result.duplicate:
            return result, ()
        source = str(source_system or "").strip()
        record = {
            "telemetry_id": "%s|%s" % (source, str(event_id or "").strip()),
            "source_system": source,
            "event_id": str(event_id or "").strip(),
            "kind": "telemetry",
        }
        with self._lock:
            callbacks = [
                (name, callback)
                for name, callback in self._deliveries.items()
                if self._delivery_sources.get(name) is None
                or source in self._delivery_sources[name]
            ]
        return result, tuple(self._deliver_one(name, callback, record) for name, callback in callbacks)

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
                    context = stored.get("context") if isinstance(stored.get("context"), Mapping) else {}
                    if (
                        str(context.get("coordinator_evaluation_kind") or "").strip().lower()
                        == "gate_check"
                    ):
                        evaluation_state_key = (market_id, source)
                        evaluation_fingerprint = str(
                            context.get("coordinator_evaluation_fingerprint") or ""
                        ).strip()
                        try:
                            transition_index = int(
                                context.get("evaluation_transition_index", 0) or 0
                            )
                        except (TypeError, ValueError):
                            transition_index = 0
                        previous_state = self._evaluation_latest.get(evaluation_state_key)
                        if (
                            previous_state is None
                            or transition_index >= int(previous_state["transition_index"])
                        ):
                            self._evaluation_latest[evaluation_state_key] = {
                                "fingerprint": evaluation_fingerprint,
                                "transition_index": transition_index,
                                "observation_id": observation_id,
                            }
                        try:
                            heartbeat_count = int(
                                context.get("evaluation_heartbeat_count", 0) or 0
                            )
                        except (TypeError, ValueError):
                            heartbeat_count = 0
                        self._evaluation_checks += 1 + max(0, heartbeat_count)
                        self._evaluation_heartbeats += max(0, heartbeat_count)
                        self._evaluation_transitions += 1
                        self._source_evaluation_checks[source] += 1 + max(0, heartbeat_count)
                        self._source_evaluation_heartbeats[source] += max(0, heartbeat_count)
                        self._source_evaluation_transitions[source] += 1
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


def configure(*, enabled: bool, persistence_enabled: Optional[bool] = None,
              persist_fn: Optional[Callable[[str, Mapping[str, Any]], bool]] = None,
              health_aggregate_fn: Optional[Callable[[], Mapping[str, Any]]] = None) -> None:
    """Configure the module-level coordinator used by legacy shadow adapters."""
    _DEFAULT_COORDINATOR.configure(
        enabled=enabled, persistence_enabled=persistence_enabled, persist_fn=persist_fn,
        health_aggregate_fn=health_aggregate_fn)


def submit_shadow(request: ObservationRequest) -> SubmissionResult:
    """Fail-open boundary for producer adapters."""
    try:
        return _DEFAULT_COORDINATOR.submit(request)
    except Exception as exc:  # defensive boundary — legacy producers must continue
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180])


def route_research(request: ObservationRequest) -> Tuple[SubmissionResult, Tuple[DeliveryResult, ...]]:
    """Use canonical intake plus registered research-only fan-out callbacks."""
    try:
        return _DEFAULT_COORDINATOR.route(request)
    except Exception as exc:  # defensive boundary — legacy producers must continue
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180]), ()


def register_delivery(destination: str, callback: Callable[[Mapping[str, Any]], Any],
                      *, sources: Optional[Sequence[str]] = None) -> None:
    """Register an injected research sink for the module-level coordinator."""
    _DEFAULT_COORDINATOR.register_delivery(destination, callback, sources=sources)


def get_report(limit: int = 100) -> Dict[str, Any]:
    """Read-only report for the coordinator diagnostic endpoint."""
    return _DEFAULT_COORDINATOR.report(limit=limit)


def record_observational_event(source_system: str, event_id: str) -> SubmissionResult:
    """Fail-open boundary for non-trade systems such as Visual Brain."""
    try:
        return _DEFAULT_COORDINATOR.record_observational_event(source_system, event_id)
    except Exception as exc:
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180])


def route_observational_event(source_system: str, event_id: str) -> Tuple[SubmissionResult, Tuple[DeliveryResult, ...]]:
    """Use canonical telemetry intake plus registered research-only fan-out."""
    try:
        return _DEFAULT_COORDINATOR.route_observational_event(source_system, event_id)
    except Exception as exc:
        return SubmissionResult(accepted=False, ignored=False, error=str(exc)[:180]), ()


def restore(records: Iterable[Mapping[str, Any]]) -> int:
    """Restore persisted shadow observations after the app's no-DDL boot probe."""
    return _DEFAULT_COORDINATOR.restore(records)


def restore_telemetry(records: Iterable[Mapping[str, Any]]) -> int:
    """Restore persisted non-trade coordinator telemetry after the no-DDL probe."""
    return _DEFAULT_COORDINATOR.restore_telemetry(records)