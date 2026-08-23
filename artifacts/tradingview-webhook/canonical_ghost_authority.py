"""Canonical Ghost Authority — shadow-only reconciliation for SCALP and INTRADAY.

This module intentionally has no app, database, broker, execution, or gateway
imports.  It creates a reporting-only canonical projection from coordinator
identities and copies of legacy lifecycle events.  It never resolves a trade,
changes a legacy record, or supplies a decision to the money path.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional


CANONICAL_MODES = frozenset(("SCALP", "INTRADAY_TREND"))
_TERMINAL_OUTCOMES = frozenset(("WIN", "LOSS", "BREAKEVEN", "EXPIRED", "CLOSED"))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    packed = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "%s_%s" % (prefix, hashlib.sha256(packed.encode("utf-8")).hexdigest()[:24])


def canonical_mode(value: Any) -> Optional[str]:
    """Return a supported canonical lane, never guessing from a strategy name."""
    mode = str(value or "").strip().upper()
    return mode if mode in CANONICAL_MODES else None


def normalize_outcome(raw_status: Any, close_reason: Any = None, result_r: Any = None) -> str:
    """Map a copied legacy outcome to a display-only comparison class.

    The raw status, close reason, and R values remain intact in each event.  This
    helper is deliberately not a resolver and must never be used to write back.
    """
    status = str(raw_status or "").strip().upper()
    reason = str(close_reason or "").strip().upper()
    if status in ("OPEN", "PENDING", "ACTIVE", ""):
        return "OPEN"
    if "EXPIRE" in status or "EXPIRE" in reason:
        return "EXPIRED"
    if "BREAKEVEN" in status or "BREAKEVEN" in reason or reason in ("BE", "BREAK_EVEN"):
        return "BREAKEVEN"
    if status in ("WIN", "WON", "TARGET", "TP", "COMPLETED"):
        return "WIN"
    if status in ("LOSS", "LOST", "STOPPED", "STOP"):
        return "LOSS"
    try:
        value = float(result_r)
    except (TypeError, ValueError):
        value = None
    if value is not None:
        if value > 0:
            return "WIN"
        if value < 0:
            return "LOSS"
        return "BREAKEVEN"
    if status == "CLOSED":
        return "CLOSED"
    return "UNKNOWN"


class CanonicalGhostAuthority:
    """Thread-safe, fail-open shadow projection over existing coordinator evidence."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._persistence_enabled = False
        self._persist_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None
        self._lock = threading.Lock()
        self._events: Dict[str, Dict[str, Any]] = {}
        self._opportunities: Dict[str, list[str]] = defaultdict(list)
        self._record_to_opportunity: Dict[tuple[str, str], str] = {}
        self._duplicates = 0
        self._ignored = 0
        self._unmatched_legacy_references = 0
        self._errors = 0
        self._persistence_writes = 0
        self._persistence_errors = 0
        self._pending_persistence: Dict[str, Dict[str, Any]] = {}
        self._restored_events = 0
        self._last_error: Optional[str] = None

    def configure(
        self,
        *,
        enabled: bool,
        persistence_enabled: Optional[bool] = None,
        persist_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    ) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if persistence_enabled is not None:
                self._persistence_enabled = bool(persistence_enabled)
                self._persist_fn = persist_fn
            elif persist_fn is not None:
                self._persist_fn = persist_fn
        self.retry_pending()

    def observe_coordinator_submission(self, record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Record a copied coordinator submission as a legacy reference.

        A coordinator market identity is the stable shared base.  The explicit
        SCALP/INTRADAY lane is added only for this new shadow projection so the
        existing coordinator IDs and persisted history remain untouched.
        """
        context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
        mode = canonical_mode(context.get("trading_mode") or context.get("mode"))
        if not mode:
            with self._lock:
                self._ignored += 1
            return None
        source_system = str(record.get("source_system") or "").strip()
        source_record_id = str(
            context.get("legacy_obs_key")
            or context.get("legacy_record_id")
            or record.get("source_event_id")
            or ""
        ).strip()
        coordinator_id = str(record.get("market_opportunity_id") or "").strip()
        if not all((source_system, source_record_id, coordinator_id)):
            return self._fail("coordinator record lacks source identity")
        canonical_opportunity_id = _stable_id(
            "cgo",
            {"coordinator_market_opportunity_id": coordinator_id, "trading_mode": mode},
        )
        # generic_ghost is the only Phase-1 authority. Other legacy systems may
        # attach as comparison references only after that authority has supplied
        # this exact stable coordinator identity; no fuzzy time/price matching.
        if source_system != "generic_ghost":
            with self._lock:
                has_authority = any(
                    self._events[event_id].get("source_system") == "generic_ghost"
                    and self._events[event_id].get("event_type") == "OBSERVED"
                    for event_id in self._opportunities.get(canonical_opportunity_id, ())
                )
                if not has_authority:
                    self._unmatched_legacy_references += 1
                    return None
        canonical_observation_id = _stable_id(
            "cobs",
            {
                "canonical_opportunity_id": canonical_opportunity_id,
                "source_system": source_system,
                "source_record_id": source_record_id,
            },
        )
        return self._record(
            {
                "event_id": _stable_id(
                    "cge",
                    {"canonical_observation_id": canonical_observation_id, "event_type": "OBSERVED"},
                ),
                "canonical_opportunity_id": canonical_opportunity_id,
                "canonical_observation_id": canonical_observation_id,
                "coordinator_market_opportunity_id": coordinator_id,
                "trading_mode": mode,
                "source_system": source_system,
                "source_record_id": source_record_id,
                "event_type": "OBSERVED",
                "legacy_table": str(context.get("legacy_table") or "unknown"),
                "raw_status": str(context.get("legacy_status") or "open"),
                "raw_close_reason": context.get("legacy_close_reason"),
                "normalized_outcome": "OPEN",
                "event_at": context.get("legacy_event_at") or record.get("signal_time"),
                "payload": {
                    "coordinator_observation_id": record.get("observation_id"),
                    "strategy_name": record.get("strategy_name"),
                    "setup_family": record.get("setup_family"),
                    "direction": record.get("direction"),
                    "entry": record.get("entry"),
                    "stop": record.get("stop"),
                    "targets": record.get("targets"),
                },
            }
        )

    def observe_legacy_outcome(
        self,
        *,
        source_system: str,
        source_record_id: str,
        raw_status: Any,
        close_reason: Any = None,
        gross_r: Any = None,
        cost_r: Any = None,
        net_r: Any = None,
        result_r: Any = None,
        exit_price: Any = None,
        mfe_r: Any = None,
        mae_r: Any = None,
        bars_held: Any = None,
        event_at: Any = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Attach a copied terminal legacy outcome without invoking a resolver."""
        key = (str(source_system or "").strip(), str(source_record_id or "").strip())
        if not all(key):
            return self._fail("legacy outcome lacks source identity")
        with self._lock:
            if not self._enabled:
                self._ignored += 1
                return None
            canonical_opportunity_id = self._record_to_opportunity.get(key)
            observed = [
                self._events[event_id]
                for event_id in self._opportunities.get(canonical_opportunity_id or "", ())
                if self._events[event_id].get("source_system") == key[0]
                and self._events[event_id].get("source_record_id") == key[1]
                and self._events[event_id].get("event_type") == "OBSERVED"
            ]
        if not canonical_opportunity_id or not observed:
            return self._fail("no canonical observation for legacy outcome")
        base = observed[-1]
        normalized = normalize_outcome(raw_status, close_reason, result_r if result_r is not None else net_r)
        return self._record(
            {
                "event_id": _stable_id(
                    "cge",
                    {
                        "canonical_observation_id": base["canonical_observation_id"],
                        "event_type": "OUTCOME_RESOLVED",
                        "raw_status": str(raw_status or ""),
                        "raw_close_reason": str(close_reason or ""),
                        "result_r": result_r if result_r is not None else net_r,
                    },
                ),
                "canonical_opportunity_id": canonical_opportunity_id,
                "canonical_observation_id": base["canonical_observation_id"],
                "coordinator_market_opportunity_id": base["coordinator_market_opportunity_id"],
                "trading_mode": base["trading_mode"],
                "source_system": key[0],
                "source_record_id": key[1],
                "event_type": "OUTCOME_RESOLVED",
                "legacy_table": base["legacy_table"],
                "raw_status": str(raw_status or ""),
                "raw_close_reason": str(close_reason or "") or None,
                "normalized_outcome": normalized,
                "gross_r": gross_r,
                "cost_r": cost_r,
                "net_r": net_r,
                "result_r": result_r,
                "exit_price": exit_price,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "bars_held": bars_held,
                "event_at": event_at,
                "payload": _json_safe(payload or {}),
            }
        )

    def restore(self, records: Iterable[Mapping[str, Any]]) -> int:
        restored = 0
        for record in records:
            result = self._record(dict(record), restore=True)
            if result is not None:
                restored += 1
        with self._lock:
            self._restored_events += restored
        return restored

    def report(self, limit: int = 100) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            opportunities = []
            by_mode = Counter()
            for canonical_id, event_ids in self._opportunities.items():
                events = [self._events[event_id] for event_id in event_ids]
                observed = [row for row in events if row["event_type"] == "OBSERVED"]
                outcomes = [row for row in events if row["event_type"] == "OUTCOME_RESOLVED"]
                if not observed:
                    continue
                base = observed[0]
                by_mode[base["trading_mode"]] += 1
                sources = sorted({row["source_system"] for row in observed})
                authority_outcomes = [
                    row for row in outcomes if row["source_system"] == "generic_ghost"
                ]
                canonical_outcome = (
                    authority_outcomes[-1]["normalized_outcome"]
                    if authority_outcomes else "OPEN"
                )
                comparison_outcomes = [
                    row["normalized_outcome"]
                    for row in outcomes
                    if row["source_system"] != "generic_ghost"
                    and row["normalized_outcome"] in _TERMINAL_OUTCOMES
                ]
                if canonical_outcome not in _TERMINAL_OUTCOMES or not comparison_outcomes:
                    agreement = "NO_COMPARISON"
                elif all(item == canonical_outcome for item in comparison_outcomes):
                    agreement = "AGREES"
                else:
                    agreement = "DISAGREES"
                opportunities.append(
                    {
                        "canonical_opportunity_id": canonical_id,
                        "canonical_observation_count": sum(
                            1 for row in observed if row["source_system"] == "generic_ghost"
                        ),
                        "coordinator_market_opportunity_id": base["coordinator_market_opportunity_id"],
                        "trading_mode": base["trading_mode"],
                        "source_systems": sources,
                        "legacy_representations": len(observed),
                        "canonical_outcome_authority": "generic_ghost_observation_lifecycle",
                        "canonical_outcome": canonical_outcome,
                        "outcome_agreement": agreement,
                        "comparison_outcomes": comparison_outcomes,
                    }
                )
            opportunities.sort(key=lambda row: (-row["legacy_representations"], row["canonical_opportunity_id"]))
            return {
                "ok": True,
                "phase": 1,
                "shadow_only": True,
                "enabled": self._enabled,
                "persistence_enabled": self._persistence_enabled,
                "canonical_modes": sorted(CANONICAL_MODES),
                "canonical_outcome_authority": "generic_ghost_observation_lifecycle",
                "unique_canonical_opportunities": len(opportunities),
                "unique_canonical_observations": sum(row["canonical_observation_count"] for row in opportunities),
                "by_mode": dict(sorted(by_mode.items())),
                "duplicate_events": self._duplicates,
                "ignored_noncanonical_events": self._ignored,
                "unmatched_legacy_references": self._unmatched_legacy_references,
                "errors": self._errors,
                "last_error": self._last_error,
                "persistence_writes": self._persistence_writes,
                "persistence_errors": self._persistence_errors,
                "pending_persistence_events": len(self._pending_persistence),
                "restored_events": self._restored_events,
                "cross_source_match_count": sum(1 for row in opportunities if len(row["source_systems"]) > 1),
                "outcome_agreement": dict(Counter(row["outcome_agreement"] for row in opportunities)),
                "opportunities": opportunities[:safe_limit],
            }

    def _record(self, event: Mapping[str, Any], *, restore: bool = False) -> Optional[Dict[str, Any]]:
        stored = _json_safe(dict(event))
        event_id = str(stored.get("event_id") or "").strip()
        required = (
            "canonical_opportunity_id", "canonical_observation_id", "coordinator_market_opportunity_id",
            "trading_mode", "source_system", "source_record_id", "event_type",
        )
        if not event_id or not all(str(stored.get(field) or "").strip() for field in required):
            return self._fail("canonical event is incomplete")
        if canonical_mode(stored["trading_mode"]) is None:
            return self._fail("canonical event has unsupported trading mode")
        with self._lock:
            if not restore and not self._enabled:
                self._ignored += 1
                return None
            if event_id in self._events:
                self._duplicates += 1
                return dict(self._events[event_id])
            self._events[event_id] = stored
            self._opportunities[stored["canonical_opportunity_id"]].append(event_id)
            if stored["event_type"] == "OBSERVED":
                self._record_to_opportunity[
                    (stored["source_system"], stored["source_record_id"])
                ] = stored["canonical_opportunity_id"]
        if not restore:
            self.retry_pending()
            self._persist(stored)
        return dict(stored)

    def _persist(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            enabled, persist_fn = self._persistence_enabled, self._persist_fn
        if not enabled or persist_fn is None:
            return
        try:
            if persist_fn(event):
                with self._lock:
                    self._persistence_writes += 1
                    self._pending_persistence.pop(str(event["event_id"]), None)
                return
        except Exception:
            pass
        with self._lock:
            self._persistence_errors += 1
            self._pending_persistence[str(event["event_id"])] = dict(event)

    def retry_pending(self) -> int:
        """Retry copied events after a transient sidecar DB failure.

        This does not affect a legacy write or resolver.  On restart, app-level
        recovery reconstructs any still-missing copies from the durable
        coordinator + generic ghost ledgers using the exact obs_key join.
        """
        with self._lock:
            enabled, persist_fn = self._persistence_enabled, self._persist_fn
            pending = list(self._pending_persistence.values())
        if not enabled or persist_fn is None or not pending:
            return 0
        persisted = 0
        for event in pending:
            try:
                wrote = bool(persist_fn(event))
            except Exception:
                wrote = False
            with self._lock:
                if wrote:
                    self._persistence_writes += 1
                    self._pending_persistence.pop(str(event["event_id"]), None)
                    persisted += 1
                else:
                    self._persistence_errors += 1
        return persisted

    def pending_event_count(self) -> int:
        with self._lock:
            return len(self._pending_persistence)

    def _fail(self, message: str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = str(message)[:180]
        return None


_DEFAULT_AUTHORITY = CanonicalGhostAuthority(enabled=False)


def configure(**kwargs: Any) -> None:
    _DEFAULT_AUTHORITY.configure(**kwargs)


def observe_coordinator_submission(record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return _DEFAULT_AUTHORITY.observe_coordinator_submission(record)
    except Exception:
        return None


def observe_legacy_outcome(**kwargs: Any) -> Optional[Dict[str, Any]]:
    try:
        return _DEFAULT_AUTHORITY.observe_legacy_outcome(**kwargs)
    except Exception:
        return None


def restore(records: Iterable[Mapping[str, Any]]) -> int:
    return _DEFAULT_AUTHORITY.restore(records)


def get_report(limit: int = 100) -> Dict[str, Any]:
    return _DEFAULT_AUTHORITY.report(limit=limit)