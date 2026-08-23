"""Durable Canonical Ghost evidence projection.

This is a shadow-only, one-record projection of the existing generic ghost
lifecycle.  It has no database, app, resolver, gate, broker, or execution
imports.  The host supplies an optional persistence callback after its
readiness probe succeeds.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from canonical_ghost_authority import CANONICAL_MODES, canonical_mode, normalize_outcome


_TERMINAL_OUTCOMES = frozenset(("WIN", "LOSS", "BREAKEVEN", "EXPIRED", "CLOSED"))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    packed = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "%s_%s" % (prefix, hashlib.sha256(packed.encode("utf-8")).hexdigest()[:24])


def _iso_time(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class CanonicalGhostEvidence:
    """Exact-ID, replay-safe projection of eligible generic ghost results."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._persistence_enabled = False
        self._persist_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None
        self._lock = threading.Lock()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._source_to_evidence: Dict[tuple[str, str, str], str] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._duplicates = 0
        self._ignored = 0
        self._errors = 0
        self._persistence_errors = 0
        self._persistence_writes = 0
        self._restored = 0
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

    def observe_submission(
        self, record: Mapping[str, Any], canonical_event: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Create the one durable result record after canonical observation accepts it."""
        if str(canonical_event.get("event_type") or "") != "OBSERVED":
            return None
        mode = canonical_mode(canonical_event.get("trading_mode"))
        source_system = str(canonical_event.get("source_system") or "").strip()
        source_result_id = str(canonical_event.get("source_record_id") or "").strip()
        canonical_observation_id = str(canonical_event.get("canonical_observation_id") or "").strip()
        coordinator_observation_id = str(record.get("observation_id") or "").strip()
        if not self._enabled:
            with self._lock:
                self._ignored += 1
            return None
        # Phase 1 keeps generic_ghost as the only outcome authority. References
        # remain in the append-only reconciliation stream, not this evidence row.
        if source_system != "generic_ghost" or mode not in CANONICAL_MODES:
            return None
        if not all((source_result_id, canonical_observation_id, coordinator_observation_id)):
            return self._fail("eligible evidence lacks exact identity")
        evidence_id = _stable_id(
            "cgev",
            {
                "canonical_observation_id": canonical_observation_id,
                "source_system": source_system,
                "source_result_id": source_result_id,
            },
        )
        payload = canonical_event.get("payload") if isinstance(canonical_event.get("payload"), Mapping) else {}
        stored = {
            "evidence_id": evidence_id,
            "canonical_opportunity_id": canonical_event.get("canonical_opportunity_id"),
            "canonical_observation_id": canonical_observation_id,
            "coordinator_market_opportunity_id": canonical_event.get("coordinator_market_opportunity_id"),
            "coordinator_observation_id": coordinator_observation_id,
            "trading_mode": mode,
            "source_system": source_system,
            "source_result_id": source_result_id,
            "legacy_table": canonical_event.get("legacy_table") or "ghost_observations",
            "strategy_name": record.get("strategy_name") or payload.get("strategy_name") or "UNKNOWN",
            "strategy_version": str(record.get("strategy_version") or ""),
            "setup_family": record.get("setup_family") or payload.get("setup_family") or "UNKNOWN",
            "instrument": record.get("instrument"),
            "timeframe": record.get("timeframe"),
            "direction": record.get("direction") or payload.get("direction"),
            "signal_time": _iso_time(record.get("signal_time") or canonical_event.get("event_at")),
            "source_bar_time": _iso_time(record.get("source_bar_time")),
            "entry_price": record.get("entry") if record.get("entry") is not None else payload.get("entry"),
            "stop_price": record.get("stop") if record.get("stop") is not None else payload.get("stop"),
            "targets": _json_safe(record.get("targets") if record.get("targets") is not None else payload.get("targets") or ()),
            "result_state": "OBSERVED",
            "outcome_version": None,
            "outcome_order_key": "",
            "raw_status": None,
            "raw_close_reason": None,
            "normalized_outcome": "OPEN",
            "gross_r": None,
            "cost_r": None,
            "net_r": None,
            "result_r": None,
            "exit_price": None,
            "mfe_r": None,
            "mae_r": None,
            "bars_held": None,
            "outcome_at": None,
            "context": _json_safe(record.get("context") or {}),
            "outcome_payload": {},
        }
        return self._upsert(stored)

    def observe_outcome(self, canonical_event: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Copy a terminal authority outcome into its existing exact evidence row."""
        if str(canonical_event.get("event_type") or "") != "OUTCOME_RESOLVED":
            return None
        mode = canonical_mode(canonical_event.get("trading_mode"))
        source_system = str(canonical_event.get("source_system") or "").strip()
        source_result_id = str(canonical_event.get("source_record_id") or "").strip()
        if source_system != "generic_ghost" or mode not in CANONICAL_MODES:
            return None
        key = (mode, source_system, source_result_id)
        with self._lock:
            evidence_id = self._source_to_evidence.get(key)
            base = dict(self._records[evidence_id]) if evidence_id else None
        if base is None:
            return self._fail("terminal outcome has no exact evidence record")
        normalized = str(canonical_event.get("normalized_outcome") or normalize_outcome(
            canonical_event.get("raw_status"),
            canonical_event.get("raw_close_reason"),
            canonical_event.get("result_r") if canonical_event.get("result_r") is not None else canonical_event.get("net_r"),
        ))
        if normalized not in _TERMINAL_OUTCOMES:
            return None
        outcome_at = _iso_time(canonical_event.get("event_at"))
        outcome_version = _stable_id(
            "cov",
            {
                "canonical_observation_id": base["canonical_observation_id"],
                "raw_status": canonical_event.get("raw_status"),
                "raw_close_reason": canonical_event.get("raw_close_reason"),
                "normalized_outcome": normalized,
                "gross_r": canonical_event.get("gross_r"),
                "cost_r": canonical_event.get("cost_r"),
                "net_r": canonical_event.get("net_r"),
                "result_r": canonical_event.get("result_r"),
                "exit_price": canonical_event.get("exit_price"),
                "mfe_r": canonical_event.get("mfe_r"),
                "mae_r": canonical_event.get("mae_r"),
                "bars_held": canonical_event.get("bars_held"),
                "outcome_at": outcome_at,
            },
        )
        # The tuple is deterministic: replayed values collapse, and a correction
        # only wins when its explicit legacy terminal timestamp is newer. The
        # fingerprint makes equal timestamps deterministic without fuzzy matching.
        outcome_order_key = "%s|%s" % (outcome_at, outcome_version)
        existing_key = str(base.get("outcome_order_key") or "")
        if existing_key and outcome_order_key <= existing_key:
            with self._lock:
                self._duplicates += 1
            return base
        base.update(
            result_state="TERMINAL",
            outcome_version=outcome_version,
            outcome_order_key=outcome_order_key,
            raw_status=canonical_event.get("raw_status"),
            raw_close_reason=canonical_event.get("raw_close_reason"),
            normalized_outcome=normalized,
            gross_r=canonical_event.get("gross_r"),
            cost_r=canonical_event.get("cost_r"),
            net_r=canonical_event.get("net_r"),
            result_r=canonical_event.get("result_r"),
            exit_price=canonical_event.get("exit_price"),
            mfe_r=canonical_event.get("mfe_r"),
            mae_r=canonical_event.get("mae_r"),
            bars_held=canonical_event.get("bars_held"),
            outcome_at=outcome_at,
            outcome_payload=_json_safe(canonical_event.get("payload") or {}),
        )
        return self._upsert(base)

    def restore(self, records: Iterable[Mapping[str, Any]]) -> int:
        restored = 0
        for record in records:
            if self._upsert(dict(record), restore=True) is not None:
                restored += 1
        with self._lock:
            self._restored += restored
        return restored

    def report(self) -> Dict[str, Any]:
        with self._lock:
            rows = list(self._records.values())
            by_mode = {
                mode: {
                    "records": sum(1 for row in rows if row.get("trading_mode") == mode),
                    "terminal_records": sum(
                        1 for row in rows
                        if row.get("trading_mode") == mode and row.get("result_state") == "TERMINAL"
                    ),
                }
                for mode in sorted(CANONICAL_MODES)
            }
            return {
                "ok": True,
                "shadow_only": True,
                "canonical_outcome_authority": "generic_ghost_observation_lifecycle",
                "enabled": self._enabled,
                "persistence_enabled": self._persistence_enabled,
                "records": len(rows),
                "by_mode": by_mode,
                "duplicates": self._duplicates,
                "ignored": self._ignored,
                "errors": self._errors,
                "last_error": self._last_error,
                "persistence_writes": self._persistence_writes,
                "persistence_errors": self._persistence_errors,
                "pending_records": len(self._pending),
                "restored_records": self._restored,
            }

    def _upsert(self, record: Mapping[str, Any], *, restore: bool = False) -> Optional[Dict[str, Any]]:
        stored = _json_safe(dict(record))
        evidence_id = str(stored.get("evidence_id") or "").strip()
        mode = canonical_mode(stored.get("trading_mode"))
        source_system = str(stored.get("source_system") or "").strip()
        source_result_id = str(stored.get("source_result_id") or "").strip()
        required = (
            "canonical_opportunity_id", "canonical_observation_id",
            "coordinator_market_opportunity_id", "coordinator_observation_id",
        )
        if not evidence_id or mode not in CANONICAL_MODES or source_system != "generic_ghost" or not source_result_id:
            return self._fail("evidence record is incomplete or ineligible")
        if not all(str(stored.get(field) or "").strip() for field in required):
            return self._fail("evidence record lacks chain identity")
        key = (mode, source_system, source_result_id)
        with self._lock:
            existing_id = self._source_to_evidence.get(key)
            if existing_id and existing_id != evidence_id:
                return self._fail("source identity maps to conflicting evidence")
            existing = self._records.get(evidence_id)
            if existing:
                old_order = str(existing.get("outcome_order_key") or "")
                new_order = str(stored.get("outcome_order_key") or "")
                if old_order and (not new_order or new_order <= old_order):
                    if not restore:
                        self._duplicates += 1
                    return dict(existing)
                if not old_order and not new_order:
                    if not restore:
                        self._duplicates += 1
                    return dict(existing)
            self._records[evidence_id] = stored
            self._source_to_evidence[key] = evidence_id
        if not restore:
            self.retry_pending()
            self._persist(stored)
        return dict(stored)

    def _persist(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            enabled, persist_fn = self._persistence_enabled, self._persist_fn
        if not enabled or persist_fn is None:
            return
        try:
            if persist_fn(record):
                with self._lock:
                    self._persistence_writes += 1
                    self._pending.pop(str(record["evidence_id"]), None)
                return
        except Exception:
            pass
        with self._lock:
            self._persistence_errors += 1
            self._pending[str(record["evidence_id"])] = dict(record)

    def retry_pending(self) -> int:
        with self._lock:
            enabled, persist_fn = self._persistence_enabled, self._persist_fn
            pending = list(self._pending.values())
        if not enabled or persist_fn is None:
            return 0
        retried = 0
        for record in pending:
            try:
                wrote = bool(persist_fn(record))
            except Exception:
                wrote = False
            with self._lock:
                if wrote:
                    self._persistence_writes += 1
                    self._pending.pop(str(record["evidence_id"]), None)
                    retried += 1
                else:
                    self._persistence_errors += 1
        return retried

    def _fail(self, message: str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = str(message)[:180]
        return None