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
from datetime import datetime, timedelta, timezone
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


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse an event timestamp for display-only age calculations."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_datetime(value: Any) -> Optional[str]:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else None


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
        self._unmatched_by_mode: Counter[str] = Counter()
        self._matched_reference_keys: set[tuple[str, str]] = set()
        self._duplicates_by_mode: Counter[str] = Counter()
        self._errors = 0
        self._persistence_writes = 0
        self._persistence_errors = 0
        self._persistence_writes_by_mode: Counter[str] = Counter()
        self._persistence_errors_by_mode: Counter[str] = Counter()
        self._pending_persistence: Dict[str, Dict[str, Any]] = {}
        self._restored_events = 0
        self._last_error: Optional[str] = None
        self._last_successful_write_at: Optional[str] = None
        self._last_successful_write_at_by_mode: Dict[str, str] = {}
        self._last_reconciliation_at: Optional[str] = None
        self._last_reconciliation_at_by_mode: Dict[str, str] = {}

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
                # An exact-ID reference rejected for lack of generic authority
                # is evidence too. Persist its append-only telemetry so health
                # coverage cannot become falsely optimistic after a restart.
                self._record(
                    {
                        "event_id": _stable_id(
                            "cge",
                            {
                                "canonical_opportunity_id": canonical_opportunity_id,
                                "source_system": source_system,
                                "source_record_id": source_record_id,
                                "event_type": "REFERENCE_UNMATCHED",
                            },
                        ),
                        "canonical_opportunity_id": canonical_opportunity_id,
                        "canonical_observation_id": _stable_id(
                            "cobs",
                            {
                                "canonical_opportunity_id": canonical_opportunity_id,
                                "source_system": source_system,
                                "source_record_id": source_record_id,
                            },
                        ),
                        "coordinator_market_opportunity_id": coordinator_id,
                        "trading_mode": mode,
                        "source_system": source_system,
                        "source_record_id": source_record_id,
                        "event_type": "REFERENCE_UNMATCHED",
                        "legacy_table": str(context.get("legacy_table") or "unknown"),
                        "raw_status": str(context.get("legacy_status") or "open"),
                        "normalized_outcome": "OPEN",
                        "event_at": context.get("legacy_event_at") or record.get("signal_time"),
                        "payload": {
                            "reason": "generic_authority_not_observed",
                            "coordinator_observation_id": record.get("observation_id"),
                        },
                    }
                )
                return None
            with self._lock:
                self._matched_reference_keys.add((source_system, source_record_id))
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
            self._rebuild_reference_index_locked()
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

    def health_report(
        self,
        *,
        coordinator_report: Optional[Mapping[str, Any]] = None,
        durable_report: Optional[Mapping[str, Any]] = None,
        legacy_report: Optional[Mapping[str, Any]] = None,
        now: Any = None,
        overdue_after_minutes: int = 240,
    ) -> Dict[str, Any]:
        """Return a read-only operational health contract.

        This summarizes existing shadow evidence only. It never creates an
        observation, resolves an outcome, changes a legacy row, or determines
        eligibility. ``durable_report`` and ``legacy_report`` are SELECT-only
        supplements supplied by the host application.
        """
        current = _parse_datetime(now) or datetime.now(timezone.utc)
        threshold_minutes = max(1, int(overdue_after_minutes))
        overdue_before = current - timedelta(minutes=threshold_minutes)
        coordinator = dict(coordinator_report or {})
        durable = dict(durable_report or {})
        legacy = dict(legacy_report or {})

        with self._lock:
            opportunities_by_mode: Dict[str, list[tuple[Dict[str, Any], list[Dict[str, Any]], list[Dict[str, Any]]]]] = defaultdict(list)
            for canonical_id, event_ids in self._opportunities.items():
                events = [self._events[event_id] for event_id in event_ids]
                observed = [row for row in events if row["event_type"] == "OBSERVED"]
                outcomes = [row for row in events if row["event_type"] == "OUTCOME_RESOLVED"]
                if observed:
                    opportunities_by_mode[observed[0]["trading_mode"]].append(
                        (observed[0], observed, outcomes)
                    )

            duplicate_events = int(self._duplicates)
            ignored_events = int(self._ignored)
            unmatched_references = int(self._unmatched_legacy_references)
            matched_reference_count = len(self._matched_reference_keys)
            persistence_writes = int(self._persistence_writes)
            persistence_errors = int(self._persistence_errors)
            pending_events = list(self._pending_persistence.values())
            last_write = self._last_successful_write_at
            last_write_by_mode = dict(self._last_successful_write_at_by_mode)
            last_reconciliation = self._last_reconciliation_at
            last_reconciliation_by_mode = dict(self._last_reconciliation_at_by_mode)
            duplicates_by_mode = Counter()
            persistence_writes_by_mode = Counter(self._persistence_writes_by_mode)
            persistence_errors_by_mode = Counter(self._persistence_errors_by_mode)
            unmatched_by_mode = Counter(self._unmatched_by_mode)
            matched_reference_keys = set(self._matched_reference_keys)
            duplicates_by_mode.update(self._duplicates_by_mode)

        by_mode: Dict[str, Dict[str, Any]] = {}
        total_observations = 0
        total_opportunities = 0
        total_unresolved = 0
        total_stale = 0
        total_overdue = 0
        total_agreements = 0
        total_disagreements = 0
        total_comparisons = 0
        total_matched = 0
        total_unmatched = 0

        for mode in sorted(CANONICAL_MODES):
            rows = opportunities_by_mode.get(mode, [])
            mode_observations = 0
            unresolved = 0
            stale = 0
            overdue = 0
            outcome_agreements = 0
            outcome_disagreements = 0
            outcome_no_comparison = 0
            outcome_comparisons = 0
            latest_observation = None
            latest_outcome = None
            matched = len([
                key for key in matched_reference_keys
                if any(
                    any(
                        observed_row.get("source_system") == key[0]
                        and observed_row.get("source_record_id") == key[1]
                        for observed_row in row[1]
                    )
                    for row in rows
                )
            ])
            for base, observed, outcomes in rows:
                mode_observations += len(observed)
                observed_at = _parse_datetime(base.get("event_at"))
                if observed_at and (latest_observation is None or observed_at > latest_observation):
                    latest_observation = observed_at
                authority_outcomes = [
                    row for row in outcomes
                    if row.get("source_system") == "generic_ghost"
                ]
                canonical_outcome = (
                    authority_outcomes[-1].get("normalized_outcome", "OPEN")
                    if authority_outcomes else "OPEN"
                )
                if canonical_outcome not in _TERMINAL_OUTCOMES:
                    unresolved += 1
                    if observed_at and observed_at < overdue_before:
                        overdue += 1
                if observed_at and observed_at < overdue_before:
                    stale += 1
                for outcome in outcomes:
                    outcome_at = _parse_datetime(outcome.get("event_at"))
                    if outcome_at and (latest_outcome is None or outcome_at > latest_outcome):
                        latest_outcome = outcome_at
                comparisons = [
                    row.get("normalized_outcome")
                    for row in outcomes
                    if row.get("source_system") != "generic_ghost"
                    and row.get("normalized_outcome") in _TERMINAL_OUTCOMES
                ]
                if canonical_outcome not in _TERMINAL_OUTCOMES or not comparisons:
                    outcome_no_comparison += 1
                elif all(item == canonical_outcome for item in comparisons):
                    outcome_agreements += 1
                    outcome_comparisons += len(comparisons)
                else:
                    outcome_disagreements += 1
                    outcome_comparisons += len(comparisons)

            reference_total = matched + int(unmatched_by_mode[mode])
            dedup_denominator = mode_observations + int(duplicates_by_mode[mode])
            mode_pending = sum(
                1 for event in pending_events
                if canonical_mode(event.get("trading_mode")) == mode
            )
            mode_durable = durable.get("by_mode", {}).get(mode, {})
            mode_legacy = legacy.get("by_mode", {}).get(mode, {})
            mode_writes = int(persistence_writes_by_mode[mode])
            mode_write_errors = int(persistence_errors_by_mode[mode])
            durable_event_count = mode_durable.get("durable_event_count")
            persistence_count = (
                int(durable_event_count or 0)
                if durable_event_count is not None else mode_writes
            )
            last_mode_write = last_write_by_mode.get(mode) or mode_durable.get("last_successful_write_at")
            last_mode_reconciliation = (
                last_reconciliation_by_mode.get(mode)
                or mode_durable.get("last_reconciliation_at")
            )
            coverage = (
                round((matched / reference_total) * 100, 2)
                if reference_total else None
            )
            status = "NO_DATA"
            if rows:
                status = "HEALTHY"
                if overdue or mode_pending or mode_write_errors or outcome_disagreements:
                    status = "ATTENTION"
            by_mode[mode] = {
                "intake_volume": mode_observations,
                "unique_canonical_opportunities": len(rows),
                "unique_canonical_observations": sum(
                    1 for _base, observed, _outcomes in rows
                    for row in observed if row.get("source_system") == "generic_ghost"
                ),
                "duplicate_count": int(duplicates_by_mode[mode]),
                "deduplication_rate": (
                    round((int(duplicates_by_mode[mode]) / dedup_denominator) * 100, 2)
                    if dedup_denominator else 0.0
                ),
                "persistence_writes": persistence_count,
                "session_persistence_writes": mode_writes,
                "durable_persisted_events": (
                    int(durable_event_count or 0)
                    if durable_event_count is not None else None
                ),
                "persistence_errors": mode_write_errors + int(mode_durable.get("persistence_errors", 0) or 0),
                "pending_persistence_events": mode_pending + int(mode_durable.get("pending_persistence_events", 0) or 0),
                "unresolved_observations": unresolved,
                "stale_observations": stale,
                "overdue_observations": overdue,
                "reconciliation_count": matched + int(unmatched_by_mode[mode]),
                "exact_id_match_count": matched,
                "exact_id_unmatched_count": int(unmatched_by_mode[mode]),
                "exact_id_coverage_scope": "append_only_exact_id_reference_events",
                "exact_id_match_coverage": coverage,
                "outcome_comparison_count": outcome_comparisons,
                "outcome_agreement_count": outcome_agreements,
                "outcome_disagreement_count": outcome_disagreements,
                "outcome_no_comparison_count": outcome_no_comparison,
                "legacy_authority_records": int(mode_legacy.get("records", 0) or 0),
                "legacy_authority_open_records": int(mode_legacy.get("open_records", 0) or 0),
                "status": status,
                "last_successful_write_at": _iso_datetime(last_mode_write),
                "last_reconciliation_at": _iso_datetime(last_mode_reconciliation),
                "last_observation_at": latest_observation.isoformat() if latest_observation else None,
                "last_outcome_at": latest_outcome.isoformat() if latest_outcome else None,
            }
            total_observations += mode_observations
            total_opportunities += len(rows)
            total_unresolved += unresolved
            total_stale += stale
            total_overdue += overdue
            total_agreements += outcome_agreements
            total_disagreements += outcome_disagreements
            total_comparisons += outcome_comparisons
            total_matched += matched
            total_unmatched += int(unmatched_by_mode[mode])

        durable_by_mode = durable.get("by_mode", {})
        durable_event_total = sum(
            int((durable_by_mode.get(mode, {}) or {}).get("durable_event_count", 0) or 0)
            for mode in CANONICAL_MODES
        )
        durable_available = bool(durable.get("db_ready"))
        overall_status = "NO_DATA"
        if total_observations:
            overall_status = "HEALTHY"
            if (
                total_overdue
                or persistence_errors
                or pending_events
                or total_disagreements
            ):
                overall_status = "ATTENTION"
        return {
            "ok": True,
            "contract_version": 1,
            "read_only": True,
            "shadow_only": True,
            "phase": 1,
            "health_status": overall_status,
            "generated_at": current.isoformat(),
            "stale_after_minutes": threshold_minutes,
            "canonical_modes": sorted(CANONICAL_MODES),
            "canonical_outcome_authority": "generic_ghost_observation_lifecycle",
            "strat_lab_included": False,
            "intake_volume": total_observations,
            "unique_canonical_opportunities": total_opportunities,
            "duplicate_count": duplicate_events,
            "deduplication_rate": (
                round((duplicate_events / (total_observations + duplicate_events)) * 100, 2)
                if total_observations + duplicate_events else 0.0
            ),
            "persistence": {
                "enabled": bool(self._persistence_enabled),
                "db_ready": durable_available,
                "writes": durable_event_total if durable_available else persistence_writes,
                "session_writes": persistence_writes,
                "durable_persisted_events": durable_event_total if durable_available else None,
                "errors": persistence_errors,
                "pending_events": len(pending_events),
                "last_successful_write_at": _iso_datetime(
                    durable.get("last_successful_write_at") or last_write
                ),
            },
            "reconciliation": {
                "count": total_matched + total_unmatched,
                "exact_id_matches": total_matched,
                "exact_id_unmatched": total_unmatched,
                "exact_id_match_coverage": (
                    round((total_matched / (total_matched + total_unmatched)) * 100, 2)
                    if total_matched + total_unmatched else None
                ),
                "last_reconciliation_at": _iso_datetime(
                    durable.get("last_reconciliation_at") or last_reconciliation
                ),
            },
            "outcomes": {
                "unresolved_observations": total_unresolved,
                "comparison_count": total_comparisons,
                "agreement_count": total_agreements,
                "disagreement_count": total_disagreements,
            },
            "staleness": {
                "stale_observations": total_stale,
                "overdue_observations": total_overdue,
                "threshold_minutes": threshold_minutes,
            },
            "coordinator": {
                "enabled": coordinator.get("enabled"),
                "requests_received": int(coordinator.get("requests_received", 0) or 0),
                "duplicate_submissions": int(coordinator.get("duplicate_submissions", 0) or 0),
                "malformed_or_rejected": int(coordinator.get("malformed_or_rejected", 0) or 0),
                "persistence_errors": int(coordinator.get("persistence_errors", 0) or 0),
                "fanout_enabled": coordinator.get("routing_mode") == "research_fanout",
            },
            "ignored_noncanonical_events": ignored_events,
            "unmatched_legacy_references": unmatched_references,
            "errors": int(self._errors),
            "last_error": self._last_error,
            "by_mode": by_mode,
        }

    def _rebuild_reference_index_locked(self) -> None:
        """Rebuild durable exact-reference health state from sidecar events."""
        self._matched_reference_keys.clear()
        self._unmatched_legacy_references = 0
        self._unmatched_by_mode.clear()
        for event_ids in self._opportunities.values():
            for event_id in event_ids:
                row = self._events[event_id]
                if row.get("event_type") == "REFERENCE_UNMATCHED":
                    mode = canonical_mode(row.get("trading_mode"))
                    if mode:
                        self._unmatched_legacy_references += 1
                        self._unmatched_by_mode[mode] += 1
            observed = [
                self._events[event_id]
                for event_id in event_ids
                if self._events[event_id].get("event_type") == "OBSERVED"
            ]
            has_generic_authority = any(
                row.get("source_system") == "generic_ghost"
                for row in observed
            )
            if not has_generic_authority:
                continue
            for row in observed:
                source = str(row.get("source_system") or "").strip()
                source_record_id = str(row.get("source_record_id") or "").strip()
                if source and source_record_id and source != "generic_ghost":
                    self._matched_reference_keys.add((source, source_record_id))

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
                mode = canonical_mode(stored.get("trading_mode"))
                if mode:
                    self._duplicates_by_mode[mode] += 1
                return dict(self._events[event_id])
            self._events[event_id] = stored
            self._opportunities[stored["canonical_opportunity_id"]].append(event_id)
            mode = canonical_mode(stored.get("trading_mode"))
            if mode and not restore:
                now_text = datetime.now(timezone.utc).isoformat()
                self._last_reconciliation_at = now_text
                self._last_reconciliation_at_by_mode[mode] = now_text
            if stored["event_type"] == "OBSERVED":
                self._record_to_opportunity[
                    (stored["source_system"], stored["source_record_id"])
                ] = stored["canonical_opportunity_id"]
            elif stored["event_type"] == "REFERENCE_UNMATCHED" and not restore:
                self._unmatched_legacy_references += 1
                if mode:
                    self._unmatched_by_mode[mode] += 1
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
                    mode = canonical_mode(event.get("trading_mode"))
                    now_text = datetime.now(timezone.utc).isoformat()
                    self._last_successful_write_at = now_text
                    if mode:
                        self._persistence_writes_by_mode[mode] += 1
                        self._last_successful_write_at_by_mode[mode] = now_text
                    self._pending_persistence.pop(str(event["event_id"]), None)
                return
        except Exception:
            pass
        with self._lock:
            self._persistence_errors += 1
            mode = canonical_mode(event.get("trading_mode"))
            if mode:
                self._persistence_errors_by_mode[mode] += 1
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
                    mode = canonical_mode(event.get("trading_mode"))
                    now_text = datetime.now(timezone.utc).isoformat()
                    self._last_successful_write_at = now_text
                    if mode:
                        self._persistence_writes_by_mode[mode] += 1
                        self._last_successful_write_at_by_mode[mode] = now_text
                    self._pending_persistence.pop(str(event["event_id"]), None)
                    persisted += 1
                else:
                    self._persistence_errors += 1
                    mode = canonical_mode(event.get("trading_mode"))
                    if mode:
                        self._persistence_errors_by_mode[mode] += 1
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


def health_report(**kwargs: Any) -> Dict[str, Any]:
    """Return the default shadow authority's read-only health contract."""
    return _DEFAULT_AUTHORITY.health_report(**kwargs)