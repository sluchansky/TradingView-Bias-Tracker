"""P4 append-only, asynchronous observer of final SCALP/IT verdicts only.

``observe`` does not open a database connection or wait on any I/O.  It copies a
bounded payload then submits it to a daemon worker with ``put_nowait``.  Nothing
in this module is a gate, scoring, risk, execution, coordinator, or evidence
input.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TABLE_NAME = "authoritative_verdict_history"
SUPPORTED_MODES = frozenset(("SCALP", "INTRADAY_TREND"))
_QUEUE_MAXSIZE = 512
_RETRY_DELAYS = (0.0, 0.05, 0.2)

_DB_FN: Optional[Callable[[], Any]] = None
_DB_READY = False
_WORK_QUEUE: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_WORKER: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()
_LAST_BY_SCOPE: Dict[Tuple[str, str], Tuple[str, str]] = {}
# Events are added only after a successful non-blocking queue submission and
# removed only after their INSERT succeeds.  This lets a failed head invalidate
# its queued descendants instead of allowing a dangling previous-key link.
_PENDING_BY_SCOPE: Dict[Tuple[str, str], List[dict]] = {}
_DROPPED_EVENTS = 0
_WRITTEN_EVENTS = 0
_RETRY_ATTEMPTS = 0
_PERSISTENCE_ERRORS = 0
_READINESS_ERROR: Optional[str] = None
_PERSISTENCE_ERROR: Optional[str] = None
_LAST_PROBE_ERROR: Optional[str] = None
_LAST_PROBE_AT: Optional[str] = None
_LAST_STARTUP_ERROR: Optional[str] = None
_LAST_STARTUP_AT: Optional[str] = None
_RECENT_WINDOW_SECONDS = 24 * 60 * 60
_RETRY_EVENTS = deque(maxlen=1024)
_PERSISTENCE_ERROR_EVENTS = deque(maxlen=1024)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any, depth: int = 0) -> Any:
    """Copy values without retaining mutable trading-engine state."""
    if depth > 5:
        return "<depth-limit>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {str(k)[:100]: _safe(v, depth + 1)
                for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v, depth + 1) for v in list(value)[:100]]
    return str(value)[:300]


def _text(value: Any) -> Optional[str]:
    """Return a bounded scalar suitable for a TEXT/TIMESTAMPTZ bind."""
    value = _safe(value)
    return value if isinstance(value, str) else (str(value) if isinstance(value, (bool, int, float)) else None)


def _source_timestamp(value: Any) -> Optional[str]:
    """Return a source-derived timestamp, never a local recording-time fallback."""
    value = _safe(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    return _text(value)


def _number(value: Any) -> Optional[float]:
    """Return a finite numeric bind value; structured display fields become null."""
    value = _safe(value)
    if isinstance(value, bool) or value is None:
        return None
    try:
        text = str(value).strip().rstrip("%")
        number = float(text)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _structure_state(value: Any) -> Optional[str]:
    """Extract a state label when the live result supplies the full cycle object."""
    if isinstance(value, dict):
        value = _first(value.get("state"), value.get("status"), value.get("label"))
    return _text(value)


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None and v != ""), None)


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _unique(values: List[Any]) -> List[Any]:
    out, seen = [], set()
    for value in values:
        value = _safe(value)
        key = json.dumps(value, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _direction(value: Any) -> Optional[str]:
    text = str(value or "").upper()
    if "LONG" in text:
        return "Long"
    if "SHORT" in text:
        return "Short"
    return None


def _grade(score: Any) -> str:
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "WAIT"
    return "A+" if score >= 85 else ("A" if score >= 70 else ("B" if score >= 50 else "WAIT"))


def _confidence(result: dict) -> Any:
    governor = result.get("confidence_governor") or {}
    timeline = result.get("confidence_timeline") or {}
    return _first(result.get("confidence"), result.get("confidence_pct"),
                  result.get("analyst_confidence"), governor.get("confidence"),
                  governor.get("active_confidence"), timeline.get("confidence"))


def _build_snapshot(result: dict, instrument: str, mode: str,
                    arm_state: Optional[dict], recorded_at: Optional[str] = None,
                    source_timestamp: Any = None) -> dict:
    """Create one whitelisted, final-result snapshot without importing app.py."""
    result = result if isinstance(result, dict) else {}
    gate = result.get("gate_debug") or {}
    it_ctx = result.get("intraday_trend_context") or {}
    plan = result.get("trade_plan") or {}
    diagnostics = result.get("vwap_diagnostics") or {}
    verdict = str(result.get("verdict") or "WAIT")
    strict_reason = result.get("strict_reason")
    candidate = _first(result.get("strict_direction"), result.get("gate_candidate"),
                       result.get("dominant_direction"), _direction(strict_reason),
                       _direction(verdict))
    actionable = verdict in (
        "LONG READY", "SHORT READY", "LONG EARLY READY", "SHORT EARLY READY",
    )
    score = _first(result.get("edge_score"), (result.get("edge_breakdown") or {}).get("score"))
    blockers = _unique(
        _list(result.get("blockers")) + _list(result.get("blocked_by"))
        + _list(gate.get("failed_conditions")) + _list(gate.get("blockedBy"))
        + _list(it_ctx.get("veto_reasons")) + _list(it_ctx.get("veto_codes"))
        + _list(plan.get("it_veto_code")) + _list(result.get("strict_missing"))
    )
    strict_blockers = _unique(
        _list(result.get("strict_blockers")) + _list(result.get("strict_missing"))
    )
    final_veto_reasons = [
        _safe(item) for item in _list(result.get("final_veto_reasons"))
        if isinstance(_safe(item), dict)
    ]
    waiting_for = _unique(
        _list(result.get("waiting_for")) + _list(gate.get("waiting_for"))
        + _list(it_ctx.get("ready_reduced_missing")) + _list(it_ctx.get("missing"))
    )
    freshness = _first(result.get("freshness"), result.get("data_freshness"),
                       it_ctx.get("freshness"), {})
    databento = _first(result.get("databento_health"), result.get("databento"),
                       result.get("market_data_health"), {})
    correlations = {
        "decision_id": _first(result.get("decision_id"), result.get("canonical_decision_id"),
                              (result.get("decision_contract") or {}).get("decision_id")),
        "gate_audit_id": _first(result.get("gate_audit_id"),
                                (result.get("gate_audit") or {}).get("audit_id")),
        "evidence_id": _first(result.get("evidence_id"), result.get("canonical_evidence_id"),
                              result.get("canonical_observation_id"),
                              (result.get("canonical_evidence") or {}).get("evidence_id")),
        "ghost_observation_id": _first(result.get("ghost_observation_id"),
                                       result.get("obs_key"), result.get("observation_id")),
        "setup_id": _first(result.get("setup_id"), plan.get("setup_id"),
                           (result.get("strategy_engine") or {}).get("setup_id")),
    }
    safety = {
        "execution_mode": result.get("execution_mode"),
        "execution_enabled": _first(result.get("execution_enabled"),
                                    (arm_state or {}).get("execution_enabled")),
        "arm_required": result.get("arm_required"),
        "armed": _first(result.get("armed"), (arm_state or {}).get("armed")),
        "safety_locked": _first(result.get("safety_locked"), result.get("safety_lock"),
                                (arm_state or {}).get("safety_locked")),
        "arm_reason": _first(result.get("arm_reason"), (arm_state or {}).get("reason")),
        "prop_status": result.get("prop_status"),
        "gateway_eligible": result.get("gatewayEligible"),
    }
    snapshot = {
        "instrument": str(instrument),
        "mode": str(mode),
        "candidate_direction": candidate,
        "actionable_direction": _direction(verdict),
        "actionable": actionable,
        "verdict": verdict,
        "wait_ready_state": "READY" if actionable else "WAIT",
        "blocked": bool(not actionable and blockers),
        "score": _number(score),
        "grade": _text(_first(result.get("grade"), _grade(score))),
        "confidence": _number(_confidence(result)),
        "blockers": blockers,
        # These are payload-only fields: the legacy blockers column remains
        # unchanged and no history-table schema migration is required.
        "strict_blockers": strict_blockers,
        "final_veto_reasons": final_veto_reasons,
        "waiting_for": waiting_for,
        "waiting_for_guidance": strict_reason,
        "vwap_value": _number(_first(result.get("vwap_value"), gate.get("vwap_value"))),
        "vwap_status": _text(_first(result.get("vwap_status"), gate.get("vwap_status"),
                                    diagnostics.get("vwap_source"))),
        "vwap_side": _text(_first(result.get("vwap_side"), gate.get("vwap_side"),
                                  (result.get("confluences") or {}).get("vwap_side"))),
        "vwap_wording": _text(_first(result.get("vwap_wording"), result.get("vwap_reason"),
                                     diagnostics.get("source_selection_reason"))),
        "structure_cycle_state": _structure_state(
            _first(result.get("structure_cycle_state"), result.get("structure_state"),
                   gate.get("structure_state"), it_ctx.get("structure_state"))
        ),
        # INTRADAY_TREND's resolved context owns its next confirmation/freshness
        # requirement; generic structure hints are a backward-compatible fallback.
        "structure_next_event": _text(_first(it_ctx.get("next_event"),
                                             result.get("next_structure_event"),
                                             result.get("structure_next_event"),
                                             gate.get("next_structure_event"))),
        "structure_context": {
            "label": result.get("structure_label"),
            "class": result.get("structure_class"),
            "source": gate.get("structure_source"),
            "bos": gate.get("bos_confirmed", gate.get("bos")),
            "choch": gate.get("choch_confirmed", gate.get("choch")),
            "cycle_confirmed": gate.get("structure_cycle_confirmed"),
        },
        "freshness": _safe(freshness),
        "databento_health": _safe(databento),
        "correlations": _safe(correlations),
        "safety": _safe(safety),
        "source_timestamp": _source_timestamp(_first(
            source_timestamp, result.get("source_timestamp"),
            result.get("bar_timestamp"), result.get("bar_ts"),
            (databento if isinstance(databento, dict) else {}).get("last_bar_ts"),
        )),
        "recorded_at": recorded_at or _now_iso(),
        "source_module": "full_analysis",
    }
    return _safe(snapshot)


def _snapshot_hash(snapshot: dict) -> str:
    # Wall-clock recording time must not defeat deterministic replay idempotency.
    # It is stored on the immutable row, but not used to decide whether the
    # authoritative decision payload has changed.
    stable = dict(snapshot)
    stable.pop("recorded_at", None)
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _event_key(scope: Tuple[str, str], previous_key: str, snapshot_hash: str) -> str:
    raw = "|".join((scope[0], scope[1], previous_key or "ROOT", snapshot_hash))
    return hashlib.sha256(raw.encode()).hexdigest()


def configure(db_conn_fn: Optional[Callable[[], Any]]) -> None:
    global _DB_FN
    _DB_FN = db_conn_fn


def _error_code(exc: Any, default: str) -> str:
    """Reduce DB errors to non-sensitive operational categories."""
    text = str(exc or "").lower()
    class_name = type(exc).__name__.lower()
    if "does not exist" in text or "undefinedtable" in class_name:
        return "table_missing"
    if (
        "connect" in text
        or "connection" in text
        or "database unavailable" in text
        or "operationalerror" in class_name
    ):
        return "database_connection_failed"
    return default


def _set_readiness_error(code: Optional[str]) -> None:
    global _READINESS_ERROR
    with _STATE_LOCK:
        _READINESS_ERROR = code


def _set_probe_error(code: Optional[str]) -> None:
    global _LAST_PROBE_ERROR, _LAST_PROBE_AT
    with _STATE_LOCK:
        if code is not None:
            _LAST_PROBE_ERROR = code
        _LAST_PROBE_AT = _now_iso()


def _set_startup_error(code: Optional[str]) -> None:
    global _LAST_STARTUP_ERROR, _LAST_STARTUP_AT
    with _STATE_LOCK:
        _LAST_STARTUP_ERROR = code
        _LAST_STARTUP_AT = _now_iso()


def _set_persistence_error(code: str) -> None:
    global _PERSISTENCE_ERRORS, _PERSISTENCE_ERROR
    with _STATE_LOCK:
        _PERSISTENCE_ERRORS += 1
        _PERSISTENCE_ERROR = code
        _PERSISTENCE_ERROR_EVENTS.append(time.time())


def _recent_event_count(events: deque, now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    cutoff = now - _RECENT_WINDOW_SECONDS
    while events and events[0] < cutoff:
        events.popleft()
    return len(events)


def _ensure_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, daemon=True,
                                   name="authoritative-verdict-history")
        _WORKER.start()


def check_db_ready(db_conn_fn: Optional[Callable[[], Any]]) -> bool:
    """No-DDL boot probe.  Failure disables only this observer."""
    global _DB_READY
    configure(db_conn_fn)
    _set_readiness_error(None)
    _set_probe_error(None)
    if not callable(db_conn_fn):
        _DB_READY = False
        _set_readiness_error("database_not_configured")
        _set_probe_error("database_not_configured")
        return False
    conn = None
    try:
        conn = db_conn_fn()
        if conn is None:
            _DB_READY = False
            _set_readiness_error("database_connection_unavailable")
            _set_probe_error("database_connection_unavailable")
            return False
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM authoritative_verdict_history LIMIT 1")
            cur.fetchone()
        _DB_READY = True
        _ensure_worker()
        logger.info("AuthoritativeVerdictHistory: table ready")
        return True
    except Exception as exc:
        _DB_READY = False
        code = _error_code(exc, "database_probe_failed")
        _set_readiness_error(code)
        _set_probe_error(code)
        logger.warning("AuthoritativeVerdictHistory: DB probe failed (observer disabled): %s", exc)
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def boot() -> None:
    """Seed the last event per scope so restarts retain transition identity."""
    if not _DB_READY or not callable(_DB_FN):
        return
    conn = None
    try:
        conn = _DB_FN()
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute("""
                SELECT instrument, mode, observation_key, snapshot_hash
                  FROM authoritative_verdict_history
                 WHERE mode IN ('SCALP', 'INTRADAY_TREND')
                 ORDER BY recorded_at DESC, event_id DESC
            """)
            rows = cur.fetchall() or []
        with _STATE_LOCK:
            for inst, mode, event_key, snapshot_hash in rows:
                _LAST_BY_SCOPE.setdefault(
                    (str(inst), str(mode)), (str(event_key), str(snapshot_hash))
                )
        logger.info("AuthoritativeVerdictHistory: restored %d scopes", len(_LAST_BY_SCOPE))
    except Exception as exc:
        _set_startup_error(_error_code(exc, "startup_readback_failed"))
        logger.warning("AuthoritativeVerdictHistory: readback failed (fail-open): %s", exc)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def observe(result: dict, instrument: str, mode: str,
            arm_state: Optional[dict] = None, *, source_timestamp: Any = None) -> bool:
    """Enqueue a final verdict snapshot; never waits on the database."""
    global _DROPPED_EVENTS
    mode, instrument = str(mode or "").upper(), str(instrument or "").upper()
    if mode not in SUPPORTED_MODES or not instrument or not _DB_READY:
        return False
    try:
        snapshot = _build_snapshot(
            result, instrument, mode, arm_state, source_timestamp=source_timestamp,
        )
        snapshot_hash = _snapshot_hash(snapshot)
        scope = (instrument, mode)
        with _STATE_LOCK:
            previous_key, previous_hash = _LAST_BY_SCOPE.get(scope, ("", ""))
            # Exact repeated final result is an idempotent replay.  Any changed
            # context appends a chained event; returning to an older state later
            # has a different previous_key and therefore appends too.
            if previous_hash == snapshot_hash:
                return False
            event = {
                **snapshot,
                "previous_observation_key": previous_key or None,
                "previous_snapshot_hash": previous_hash,
                "snapshot_hash": snapshot_hash,
            }
            event["observation_key"] = _event_key(scope, previous_key, snapshot_hash)
            event["payload"] = snapshot
            try:
                _WORK_QUEUE.put_nowait(event)
            except queue.Full:
                _DROPPED_EVENTS += 1
                logger.warning("AuthoritativeVerdictHistory: queue full; observation dropped")
                return False
            _PENDING_BY_SCOPE.setdefault(scope, []).append(event)
            _LAST_BY_SCOPE[scope] = (event["observation_key"], snapshot_hash)
        _ensure_worker()
        return True
    except Exception as exc:
        logger.debug("AuthoritativeVerdictHistory snapshot failed: %s", exc)
        return False


def _persist_event(event: dict) -> bool:
    """Worker-only INSERT.  It has no caller on the live-analysis stack."""
    global _WRITTEN_EVENTS
    if not callable(_DB_FN):
        _set_persistence_error("database_not_configured")
        return False
    conn = None
    try:
        conn = _DB_FN()
        if conn is None:
            _set_persistence_error("database_connection_unavailable")
            return False
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO authoritative_verdict_history (
                    observation_key, previous_observation_key, snapshot_hash,
                    instrument, mode, candidate_direction, actionable_direction,
                    actionable, verdict, wait_ready_state, blocked, score, grade,
                    confidence, blockers, waiting_for, waiting_for_guidance,
                    vwap_value, vwap_status, vwap_side, vwap_wording,
                    structure_cycle_state, structure_next_event, structure_context,
                    freshness, databento_health, correlations, safety_snapshot,
                    source_timestamp, source_module, recorded_at, payload
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT (observation_key) DO NOTHING
            """, (
                event["observation_key"], event["previous_observation_key"],
                event["snapshot_hash"], event["instrument"], event["mode"],
                event["candidate_direction"], event["actionable_direction"],
                event["actionable"], event["verdict"], event["wait_ready_state"],
                event["blocked"], event["score"], event["grade"], event["confidence"],
                json.dumps(event["blockers"]), json.dumps(event["waiting_for"]),
                event["waiting_for_guidance"], event["vwap_value"], event["vwap_status"],
                event["vwap_side"], event["vwap_wording"], event["structure_cycle_state"],
                event["structure_next_event"], json.dumps(event["structure_context"]),
                json.dumps(event["freshness"]), json.dumps(event["databento_health"]),
                json.dumps(event["correlations"]), json.dumps(event["safety"]),
                event["source_timestamp"], event["source_module"], event["recorded_at"],
                json.dumps(event["payload"]),
            ))
        conn.commit()
        _WRITTEN_EVENTS += 1
        return True
    except Exception as exc:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        _set_persistence_error(_error_code(exc, "database_write_failed"))
        logger.debug("AuthoritativeVerdictHistory write failed: %s", exc)
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _mark_persisted(event: dict) -> None:
    """Remove one durable event from the pending chain without changing its head."""
    scope = (event["instrument"], event["mode"])
    with _STATE_LOCK:
        pending = _PENDING_BY_SCOPE.get(scope, [])
        _PENDING_BY_SCOPE[scope] = [
            candidate for candidate in pending
            if candidate["observation_key"] != event["observation_key"]
        ]
        if not _PENDING_BY_SCOPE[scope]:
            _PENDING_BY_SCOPE.pop(scope, None)


def _rollback_pending(event: dict) -> None:
    """Invalidate a failed event and queued descendants, preserving valid links."""
    scope = (event["instrument"], event["mode"])
    with _STATE_LOCK:
        pending = _PENDING_BY_SCOPE.get(scope, [])
        failed_at = next(
            (index for index, candidate in enumerate(pending)
             if candidate["observation_key"] == event["observation_key"]),
            None,
        )
        if failed_at is None:
            return
        # A worker is ordered, so successors are still only queued.  Mark them
        # cancelled in place; the worker will consume and task_done them without
        # writing a row whose predecessor was never durable.
        for descendant in pending[failed_at + 1:]:
            descendant["_cancelled"] = True
        del pending[failed_at:]
        if pending:
            _PENDING_BY_SCOPE[scope] = pending
        else:
            _PENDING_BY_SCOPE.pop(scope, None)
        _LAST_BY_SCOPE[scope] = (
            event.get("previous_observation_key") or "",
            event.get("previous_snapshot_hash") or "",
        )


def _worker_loop() -> None:
    global _RETRY_ATTEMPTS
    while True:
        event = _WORK_QUEUE.get()
        try:
            if event is None:
                return
            if event.get("_cancelled"):
                continue
            written = False
            for attempt, delay in enumerate(_RETRY_DELAYS):
                if delay:
                    time.sleep(delay)
                if attempt:
                    with _STATE_LOCK:
                        _RETRY_ATTEMPTS += 1
                        _RETRY_EVENTS.append(time.time())
                if _persist_event(event):
                    written = True
                    break
            if not written:
                _rollback_pending(event)
            else:
                _mark_persisted(event)
        except Exception as exc:
            logger.debug("AuthoritativeVerdictHistory worker failure: %s", exc)
            if event is not None:
                _rollback_pending(event)
        finally:
            _WORK_QUEUE.task_done()


_HISTORY_COLUMNS = (
    "event_id", "observation_key", "previous_observation_key", "snapshot_hash",
    "instrument", "mode", "candidate_direction", "actionable_direction",
    "actionable", "verdict", "wait_ready_state", "blocked", "score", "grade",
    "confidence", "blockers", "waiting_for", "waiting_for_guidance",
    "vwap_value", "vwap_status", "vwap_side", "vwap_wording",
    "structure_cycle_state", "structure_next_event", "structure_context",
    "freshness", "databento_health", "correlations", "safety_snapshot",
    "source_timestamp", "source_module", "recorded_at", "payload",
)


def _query_history(
    instrument: str,
    mode: str,
    limit: int = 120,
    *,
    newest_window: bool = False,
) -> List[dict]:
    """Read immutable rows and surface failures to read-only report callers."""
    if not _DB_READY or not callable(_DB_FN):
        raise RuntimeError("history database unavailable")
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 120
    conn = None
    try:
        conn = _DB_FN()
        if conn is None:
            raise RuntimeError("history database unavailable")
        with conn.cursor() as cur:
            select_columns = """
                event_id, observation_key, previous_observation_key,
                snapshot_hash, instrument, mode, candidate_direction,
                actionable_direction, actionable, verdict, wait_ready_state,
                blocked, score, grade, confidence, blockers, waiting_for,
                waiting_for_guidance, vwap_value, vwap_status, vwap_side,
                vwap_wording, structure_cycle_state, structure_next_event,
                structure_context, freshness, databento_health, correlations,
                safety_snapshot, source_timestamp, source_module, recorded_at,
                payload
            """
            if newest_window:
                cur.execute(f"""
                    SELECT {select_columns}
                      FROM (
                            SELECT {select_columns}
                              FROM authoritative_verdict_history
                             WHERE instrument = %s AND mode = %s
                             ORDER BY event_id DESC
                             LIMIT %s
                           ) AS latest_history
                     ORDER BY event_id ASC
                """, (str(instrument).upper(), str(mode).upper(), limit))
            else:
                cur.execute(f"""
                    SELECT {select_columns}
                      FROM authoritative_verdict_history
                     WHERE instrument = %s AND mode = %s
                     ORDER BY recorded_at ASC, event_id ASC
                     LIMIT %s
                """, (str(instrument).upper(), str(mode).upper(), limit))
            rows = cur.fetchall() or []
        return [dict(zip(_HISTORY_COLUMNS, row)) for row in rows]
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def get_history(instrument: str, mode: str, limit: int = 120) -> List[dict]:
    """Read immutable rows for reconstruction; never called by live trading."""
    try:
        return _query_history(instrument, mode, limit)
    except Exception as exc:
        logger.debug("AuthoritativeVerdictHistory read failed: %s", exc)
        return []


def get_history_report(instrument: str, mode: str, limit: int = 120) -> dict:
    """Return a curated operator timeline with explicit availability and links."""
    instrument = str(instrument or "").upper()
    mode = str(mode or "").upper()
    base = {
        "ok": False,
        "available": False,
        "read_only": True,
        "observer_only": True,
        "instrument": instrument,
        "mode": mode,
        "count": 0,
        "chain": {
            "status": "EMPTY",
            "roots": 0,
            "contiguous": 0,
            "breaks": 0,
            "partial": False,
        },
        "events": [],
    }
    if not _DB_READY or not callable(_DB_FN):
        return {**base, "error": "history_unavailable"}

    try:
        requested_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        requested_limit = 120
    try:
        rows = _query_history(
            instrument,
            mode,
            min(requested_limit + 1, 2000),
            newest_window=True,
        )
    except Exception as exc:
        logger.debug("AuthoritativeVerdictHistory operator read failed: %s", exc)
        return {**base, "error": _error_code(exc, "history_query_failed")}

    window_truncated = len(rows) > requested_limit
    outside_window_key = (
        _text(rows[0].get("observation_key")) if window_truncated else None
    )
    if window_truncated:
        rows = rows[-requested_limit:]

    events: List[dict] = []
    roots = contiguous = breaks = 0
    partial = False
    prior_key: Optional[str] = None
    for index, row in enumerate(rows):
        observation_key = _text(row.get("observation_key")) or ""
        previous_key = _text(row.get("previous_observation_key"))
        expected_previous = prior_key
        if index == 0:
            if previous_key is None:
                chain_status = "ROOT"
                roots += 1
            elif window_truncated and previous_key == outside_window_key:
                chain_status = "WINDOW_START"
                partial = True
            else:
                chain_status = "BROKEN"
                breaks += 1
        elif previous_key == prior_key:
            chain_status = "CONTIGUOUS"
            contiguous += 1
        else:
            chain_status = "BROKEN"
            breaks += 1
            if previous_key is None:
                roots += 1
        events.append({
            "event_id": _safe(row.get("event_id")),
            "observation_key": observation_key,
            "previous_observation_key": previous_key,
            "recorded_at": _text(row.get("recorded_at")),
            "source_timestamp": _text(row.get("source_timestamp")),
            "verdict": _text(row.get("verdict")) or "WAIT",
            "wait_ready_state": _text(row.get("wait_ready_state")) or "WAIT",
            "actionable": bool(row.get("actionable")),
            "blocked": bool(row.get("blocked")),
            "score": _number(row.get("score")),
            "grade": _text(row.get("grade")),
            "confidence": _number(row.get("confidence")),
            "candidate_direction": _text(row.get("candidate_direction")),
            "actionable_direction": _text(row.get("actionable_direction")),
            "blockers": _safe(_list(row.get("blockers"))),
            "waiting_for": _safe(_list(row.get("waiting_for"))),
            "chain_status": chain_status,
            "chain_expected_previous": expected_previous,
        })
        prior_key = observation_key

    base.update({
        "ok": True,
        "available": True,
        "count": len(events),
        "chain": {
            "status": (
                "EMPTY" if not events
                else ("BROKEN" if breaks else ("PARTIAL" if partial else "VALID"))
            ),
            "roots": roots,
            "contiguous": contiguous,
            "breaks": breaks,
            "partial": partial,
        },
        "events": events,
    })
    return base


def _diagnostic_datetime(value: Any) -> Optional[datetime]:
    """Normalize DB, ISO, and epoch timestamps for research reconstruction."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _diagnostic_texts(row: dict, *keys: str) -> List[str]:
    values: List[str] = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item) for item in value if item is not None)
        elif value is not None:
            values.append(str(value))
    return values


def _structure_blocked(row: dict) -> bool:
    texts = _diagnostic_texts(row, "blockers", "waiting_for", "waiting_for_guidance")
    return bool(row.get("blocked")) and any(
        any(token in text.upper() for token in ("STRUCTURE", "BOS", "CHOCH"))
        for text in texts
    )


def _structure_confirmed(row: dict) -> bool:
    state = str(row.get("structure_cycle_state") or "").upper()
    if any(token in state for token in ("CONTINUATION", "CONFIRMED", "TREND_CONFIRMED")):
        return True
    context = row.get("structure_context") or {}
    return bool(
        row.get("actionable")
        and isinstance(context, dict)
        and context.get("cycle_confirmed") is True
    )


def _structure_source_timestamp(row: dict) -> Optional[datetime]:
    context = row.get("structure_context") or {}
    if not isinstance(context, dict):
        context = {}
    payload = row.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    return _diagnostic_datetime(_first(
        context.get("source_timestamp"), context.get("event_timestamp"),
        context.get("observed_at"), payload.get("structure_source_timestamp"),
        payload.get("structure_event_timestamp"), row.get("source_timestamp"),
    ))


def build_structure_confirmation_diagnostic(
    rows: List[dict],
    *,
    instrument: Optional[str] = None,
    mode: Optional[str] = None,
    min_score: float = 70.0,
    confirmation_window_seconds: int = 600,
    source_delay_seconds: int = 120,
    detector_no_update_seconds: int = 300,
    now: Any = None,
) -> dict:
    """Reconstruct structure waits from immutable verdict snapshots.

    This is deliberately a pure, display-only reducer.  A high-score snapshot
    blocked by structure starts a case; later immutable snapshots resolve it as
    confirmed continuation, expiry, source-data delay, or detector no-update.
    """
    current = _diagnostic_datetime(now) or datetime.now(timezone.utc)
    normalized: List[Tuple[datetime, dict]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        observed = _structure_source_timestamp(row)
        recorded = _diagnostic_datetime(row.get("recorded_at"))
        at = observed or recorded
        if at is not None:
            normalized.append((at, row))
    normalized.sort(key=lambda item: item[0])
    cases: List[dict] = []
    active: Dict[Tuple[str, str], dict] = {}

    for at, row in normalized:
        direction = _direction(_first(
            row.get("candidate_direction"), row.get("actionable_direction")
        ))
        if not direction:
            continue
        scope = (str(row.get("instrument") or instrument or "").upper(), direction)
        score = _number(row.get("score"))
        is_candidate = (
            score is not None and score >= float(min_score)
            and _structure_blocked(row)
        )
        if is_candidate:
            prior = active.get(scope)
            event = _first(row.get("structure_next_event"), "structure confirmation")
            if prior is None:
                active[scope] = {
                    "started_at": at,
                    "last_at": at,
                    "source_timestamp": at.isoformat(),
                    "recorded_at": _text(row.get("recorded_at")),
                    "instrument": scope[0],
                    "mode": str(row.get("mode") or mode or "").upper(),
                    "direction": direction,
                    "score": score,
                    "grade": row.get("grade"),
                    "outstanding_event": str(event),
                    "source": "authoritative_verdict_history",
                    "source_delay_seconds": None,
                }
            else:
                prior["last_at"] = at
                prior["score"] = max(prior["score"], score)
                prior["outstanding_event"] = str(event)
            recorded = _diagnostic_datetime(row.get("recorded_at"))
            if recorded is not None:
                delay = max(0.0, (recorded - at).total_seconds())
                prior = active[scope]
                prior["source_delay_seconds"] = round(delay, 3)
                if delay >= source_delay_seconds:
                    prior["outcome"] = "SOURCE_DATA_DELAY"
                    prior["elapsed_seconds"] = round((at - prior["started_at"]).total_seconds(), 3)
                    cases.append(active.pop(scope))
                    continue
            continue

        prior = active.get(scope)
        if prior is None:
            continue
        elapsed = max(0.0, (at - prior["started_at"]).total_seconds())
        prior["last_at"] = at
        prior["elapsed_seconds"] = round(elapsed, 3)
        if _structure_confirmed(row):
            prior["outcome"] = "CONFIRMED_CONTINUATION"
            cases.append(active.pop(scope))
        elif elapsed >= confirmation_window_seconds:
            prior["outcome"] = "EXPIRED"
            cases.append(active.pop(scope))

    for scope, prior in list(active.items()):
        elapsed = max(0.0, (current - prior["started_at"]).total_seconds())
        prior["elapsed_seconds"] = round(elapsed, 3)
        if prior.get("source_delay_seconds") is not None and prior["source_delay_seconds"] >= source_delay_seconds:
            prior["outcome"] = "SOURCE_DATA_DELAY"
        elif elapsed >= confirmation_window_seconds:
            prior["outcome"] = "EXPIRED"
        elif elapsed >= detector_no_update_seconds:
            prior["outcome"] = "DETECTOR_NO_UPDATE"
        else:
            prior["outcome"] = "WAITING_REAL_CONFIRMATION"
        cases.append(active.pop(scope))

    cases.sort(key=lambda item: (item["instrument"], item["mode"], item["started_at"]))
    for case in cases:
        case["started_at"] = case["started_at"].isoformat()
        case["last_source_timestamp"] = case.pop("last_at").isoformat()
    counts = {name: sum(case["outcome"] == name for case in cases) for name in (
        "CONFIRMED_CONTINUATION", "EXPIRED", "SOURCE_DATA_DELAY",
        "DETECTOR_NO_UPDATE", "WAITING_REAL_CONFIRMATION",
    )}
    return {
        "ok": True,
        "read_only": True,
        "observer_only": True,
        "contract_version": 1,
        "filters": {
            "instrument": str(instrument).upper() if instrument else None,
            "mode": str(mode).upper() if mode else None,
            "min_score": float(min_score),
            "confirmation_window_seconds": int(confirmation_window_seconds),
            "source_delay_seconds": int(source_delay_seconds),
            "detector_no_update_seconds": int(detector_no_update_seconds),
        },
        "counts": counts,
        "cases": cases,
        "note": (
            "Research reconstruction only. Source timestamps come from the "
            "immutable verdict snapshot; this report never gates or executes."
        ),
    }


def get_structure_confirmation_diagnostic(
    instrument: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 1200,
    **kwargs: Any,
) -> dict:
    """Read immutable history and build the confirmation-lag report."""
    instruments = [str(instrument).upper()] if instrument else ["MGC", "MNQ", "MES", "MYM"]
    modes = [str(mode).upper()] if mode else list(SUPPORTED_MODES)
    rows: List[dict] = []
    for inst in instruments:
        for selected_mode in modes:
            if selected_mode not in SUPPORTED_MODES:
                continue
            rows.extend(get_history(inst, selected_mode, limit=limit))
    return build_structure_confirmation_diagnostic(
        rows, instrument=instrument, mode=mode, **kwargs
    )


def status() -> dict:
    with _STATE_LOCK:
        worker_running = bool(_WORKER is not None and _WORKER.is_alive())
        now = time.time()
        return {
            "db_ready": bool(_DB_READY),
            "observer_enabled": bool(_DB_READY),
            "worker_enabled": bool(_DB_READY),
            "worker_running": worker_running,
            "queue_depth": _WORK_QUEUE.qsize(),
            "queue_capacity": _QUEUE_MAXSIZE,
            "queue_saturated": _WORK_QUEUE.qsize() >= _QUEUE_MAXSIZE,
            "written_events": _WRITTEN_EVENTS,
            "dropped_events": _DROPPED_EVENTS,
            "retry_attempts": _RETRY_ATTEMPTS,
            "persistence_errors": _PERSISTENCE_ERRORS,
            "readiness_error": _READINESS_ERROR,
            "last_probe_error": _LAST_PROBE_ERROR,
            "last_probe_at": _LAST_PROBE_AT,
            "last_startup_error": _LAST_STARTUP_ERROR,
            "last_startup_at": _LAST_STARTUP_AT,
            "persistence_error": _PERSISTENCE_ERROR,
            "pending_events": sum(len(events) for events in _PENDING_BY_SCOPE.values()),
            "recent": {
                "window_seconds": _RECENT_WINDOW_SECONDS,
                "retry_attempts": _recent_event_count(_RETRY_EVENTS, now),
                "persistence_errors": _recent_event_count(
                    _PERSISTENCE_ERROR_EVENTS, now
                ),
            },
            "scopes": len(_LAST_BY_SCOPE),
        }


def _reset_for_tests() -> None:
    global _DB_FN, _DB_READY, _DROPPED_EVENTS, _WRITTEN_EVENTS
    global _RETRY_ATTEMPTS, _PERSISTENCE_ERRORS, _READINESS_ERROR, _PERSISTENCE_ERROR
    global _LAST_PROBE_ERROR, _LAST_PROBE_AT, _LAST_STARTUP_ERROR, _LAST_STARTUP_AT
    _DB_FN, _DB_READY = None, False
    _DROPPED_EVENTS, _WRITTEN_EVENTS = 0, 0
    _RETRY_ATTEMPTS, _PERSISTENCE_ERRORS = 0, 0
    _READINESS_ERROR, _PERSISTENCE_ERROR = None, None
    _LAST_PROBE_ERROR, _LAST_PROBE_AT = None, None
    _LAST_STARTUP_ERROR, _LAST_STARTUP_AT = None, None
    _RETRY_EVENTS.clear()
    _PERSISTENCE_ERROR_EVENTS.clear()
    with _STATE_LOCK:
        _LAST_BY_SCOPE.clear()
        _PENDING_BY_SCOPE.clear()
    while True:
        try:
            _WORK_QUEUE.get_nowait()
            _WORK_QUEUE.task_done()
        except queue.Empty:
            break