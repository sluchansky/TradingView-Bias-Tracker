"""Persistent market-student research ledger.

This module is intentionally dependency-light and application-agnostic.  It is
an additive observation system: callers provide an already-computed snapshot,
and this module never recomputes or changes a trading decision.

The durable contract is:
  observation -> immutable hypothesis -> exact terminal outcome

All database writes are best-effort.  A missing schema, unavailable database,
or malformed source snapshot disables persistence for that operation without
raising into the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

MODES = ("SCALP", "INTRADAY_TREND", "SWING")
HORIZONS = {
    "SCALP": {
        "label": "minutes",
        "expiry_minutes": 30,
        "checkpoints_minutes": (1, 3, 5, 10, 15, 30),
    },
    "INTRADAY_TREND": {
        "label": "session",
        "expiry_minutes": 360,
        "checkpoints_minutes": (15, 30, 60, 120, 240, 360),
    },
    "SWING": {
        "label": "multi_session",
        "expiry_minutes": 2880,
        "checkpoints_minutes": (60, 240, 720, 1440, 2880),
    },
}
MODEL_VERSION = "market-student-v1"
LEDGER_VERSION = 1
WAIT_HEARTBEAT_SECONDS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if value is None:
        return _now().isoformat()
    return str(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _dump(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _hash(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_dump(value).encode()).hexdigest()[:32]}"


def canonical_mode(mode: Any) -> str:
    value = str(mode or "").strip().upper()
    return value if value in MODES else "SCALP"


def canonical_direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"LONG", "BUY", "BULLISH", "UP"}:
        return "LONG"
    if text in {"SHORT", "SELL", "BEARISH", "DOWN"}:
        return "SHORT"
    return "NEUTRAL"


def is_wait(verdict: Any) -> bool:
    return "WAIT" in str(verdict or "").upper() or str(verdict or "").upper() in {
        "", "NO_TRADE", "SKIP", "CLOSED"
    }


def _direction_from_result(result: Mapping[str, Any]) -> str:
    direction = canonical_direction(
        result.get("strict_direction")
        or result.get("candidate_direction")
        or result.get("actionable_direction")
    )
    if direction != "NEUTRAL":
        return direction
    presentation = result.get("operator_presentation")
    if isinstance(presentation, Mapping):
        direction = canonical_direction(
            presentation.get("candidate_direction")
            or presentation.get("actionable_direction")
        )
    if direction != "NEUTRAL":
        return direction
    return canonical_direction(result.get("bias"))


def _plan(result: Mapping[str, Any]) -> Mapping[str, Any]:
    value = result.get("trade_plan")
    return value if isinstance(value, Mapping) else {}


def _price(result: Mapping[str, Any]) -> float | None:
    for key in ("current_price", "price", "last_price"):
        value = _safe_float(result.get(key))
        if value is not None:
            return value
    return _safe_float(result.get("entry_price"))


def _expected_move(plan: Mapping[str, Any], direction: str) -> float | None:
    entry = _safe_float(plan.get("entry"))
    target = _safe_float(plan.get("target") or plan.get("tp1") or plan.get("target1"))
    if entry is None or target is None:
        return None
    return round((target - entry) if direction == "LONG" else (entry - target), 6)


@dataclass(frozen=True)
class ForecastContract:
    contract_version: int
    mode: str
    horizon: str
    checkpoints_minutes: tuple[int, ...]
    expiry_minutes: int
    direction: str
    confidence: float | None
    probability: float | None
    entry_price: float | None
    expected_move: float | None
    invalidation_price: float | None
    targets: tuple[float, ...]
    deterministic_verdict: str
    strategy: str | None
    strategy_version: str
    data_watermark: str
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checkpoints_minutes"] = list(self.checkpoints_minutes)
        value["targets"] = list(self.targets)
        return value


def build_forecast_contract(
    result: Mapping[str, Any],
    instrument: str,
    mode: str,
    *,
    source_timestamp: Any = None,
    source_system: str = "full_analysis",
) -> ForecastContract:
    """Build a mode-specific contract from an authoritative result snapshot."""
    mode = canonical_mode(mode)
    horizon = HORIZONS[mode]
    plan = _plan(result)
    direction = _direction_from_result(result)
    entry = _safe_float(plan.get("entry"))
    stop = _safe_float(plan.get("stop") or plan.get("stop_loss"))
    targets = tuple(
        value for value in (
            _safe_float(plan.get("target") or plan.get("tp1") or plan.get("target1")),
            _safe_float(plan.get("target2") or plan.get("tp2")),
        ) if value is not None
    )
    confidence = _safe_float(result.get("confidence"))
    if confidence is None:
        confidence = _safe_float(result.get("edge_score"))
    probability = _safe_float(result.get("probability"))
    strategy = (
        result.get("strategy_key")
        or (result.get("learning_ctx") or {}).get("strategy_key")
        or (result.get("strategy_scanner") or {}).get("strategy_key")
    )
    watermark = _iso(source_timestamp or result.get("source_bar_time") or _now())
    return ForecastContract(
        contract_version=LEDGER_VERSION,
        mode=mode,
        horizon=horizon["label"],
        checkpoints_minutes=tuple(horizon["checkpoints_minutes"]),
        expiry_minutes=horizon["expiry_minutes"],
        direction=direction,
        confidence=confidence,
        probability=probability,
        entry_price=entry,
        expected_move=_expected_move(plan, direction),
        invalidation_price=stop,
        targets=targets,
        deterministic_verdict=str(result.get("verdict") or "WAIT"),
        strategy=str(strategy) if strategy else None,
        strategy_version=str(result.get("strategy_version") or MODEL_VERSION),
        data_watermark=watermark,
        provenance={
            "source_system": source_system,
            "instrument": str(instrument or "").upper(),
            "source_event_id": result.get("source_event_id"),
        },
    )


def normalize_r(
    *,
    direction: Any,
    entry: Any,
    stop: Any,
    exit_price: Any = None,
    gross_r: Any = None,
    cost_r: Any = None,
    net_r: Any = None,
) -> dict[str, float | None]:
    """Return auditable gross/cost/net R without overwriting source values."""
    entry_f, stop_f, exit_f = _safe_float(entry), _safe_float(stop), _safe_float(exit_price)
    risk = abs(entry_f - stop_f) if entry_f is not None and stop_f is not None else None
    derived = None
    if risk and exit_f is not None:
        raw = exit_f - entry_f if canonical_direction(direction) == "LONG" else entry_f - exit_f
        derived = raw / risk
    gross = _safe_float(gross_r)
    cost = _safe_float(cost_r)
    net = _safe_float(net_r)
    if gross is None:
        gross = derived
    if net is None and gross is not None:
        net = gross - cost if cost is not None else gross
    return {
        "risk_points": round(risk, 8) if risk is not None else None,
        "derived_gross_r": round(derived, 8) if derived is not None else None,
        "source_gross_r": gross,
        "source_cost_r": cost,
        "source_net_r": net,
        "normalized_gross_r": round(gross, 8) if gross is not None else None,
        "normalized_net_r": round(net, 8) if net is not None else None,
    }


def resolve_terminal_outcome(
    *,
    direction: Any,
    entry: Any,
    stop: Any,
    targets: Iterable[Any] = (),
    bars: Iterable[Mapping[str, Any]] = (),
    expiry_at: Any = None,
) -> dict[str, Any]:
    """Resolve a closed-bar sequence stop-first, target-first, or ambiguous."""
    side = canonical_direction(direction)
    entry_f, stop_f = _safe_float(entry), _safe_float(stop)
    target_values = tuple(v for v in (_safe_float(x) for x in targets) if v is not None)
    if side not in {"LONG", "SHORT"} or entry_f is None or stop_f is None:
        return {"outcome": "AMBIGUOUS", "reason": "incomplete_contract"}
    bar_rows = tuple(bars)
    for index, bar in enumerate(bar_rows, start=1):
        high, low = _safe_float(bar.get("high")), _safe_float(bar.get("low"))
        if high is None or low is None:
            continue
        stop_hit = low <= stop_f if side == "LONG" else high >= stop_f
        target_hit = any(
            high >= target if side == "LONG" else low <= target
            for target in target_values
        )
        # Conservative and deterministic when both are touched in one bar.
        if stop_hit:
            return {
                "outcome": "LOSS",
                "reason": "STOP_FIRST",
                "bars_held": index,
                "exit_price": stop_f,
            }
        if target_hit:
            target = target_values[0]
            return {
                "outcome": "WIN",
                "reason": "TARGET_FIRST",
                "bars_held": index,
                "exit_price": target,
            }
    if expiry_at is not None:
        return {"outcome": "EXPIRED", "reason": "HORIZON_EXPIRED", "bars_held": len(bar_rows)}
    return {"outcome": "PENDING", "reason": "NO_TERMINAL_TOUCH"}


class MarketStudentLedger:
    """Thread-safe in-memory projection with optional exact durable writes."""

    def __init__(
        self,
        db_conn_fn: Callable[[], Any] | None = None,
        *,
        enabled: bool = True,
        wait_heartbeat_seconds: int = WAIT_HEARTBEAT_SECONDS,
        model_version: str = MODEL_VERSION,
    ):
        self.db_conn_fn = db_conn_fn
        self.enabled = bool(enabled)
        self.persistence_enabled = False
        self.wait_heartbeat_seconds = max(1, int(wait_heartbeat_seconds))
        self.model_version = model_version
        self._lock = threading.RLock()
        self._hypotheses: dict[str, dict[str, Any]] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._outcomes: dict[str, dict[str, Any]] = {}
        self._theses: dict[tuple[str, str], dict[str, Any]] = {}
        self._reconciliation: deque[dict[str, Any]] = deque(maxlen=100000)
        self._last_wait: dict[tuple[str, str], tuple[str, float]] = {}
        self._last_alert: dict[str, dict[str, Any]] = {}
        self._stats = Counter()
        self._last_error: str | None = None
        self._write_queue: queue.Queue[list[tuple[str, tuple[Any, ...]]]] = queue.Queue(maxsize=5000)
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="market-student-writer",
            daemon=True,
        )
        self._writer.start()

    def configure(self, *, persistence_enabled: bool) -> None:
        with self._lock:
            self.persistence_enabled = bool(persistence_enabled and self.db_conn_fn)

    def restore(self) -> dict[str, int]:
        """Restore exact rows; a failed restore disables the writer."""
        if not self.persistence_enabled or not self.db_conn_fn:
            return {"observations": 0, "hypotheses": 0, "outcomes": 0}
        conn = None
        try:
            conn = self.db_conn_fn()
            if conn is None:
                raise RuntimeError("database unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT observation_id, instrument, mode, source_system, "
                    "source_event_id, source_timestamp, fingerprint, payload "
                    "FROM market_student_observations ORDER BY created_at ASC LIMIT 100000"
                )
                observations = cur.fetchall() or []
                cur.execute(
                    "SELECT hypothesis_id, observation_id, instrument, mode, "
                    "contract, created_at FROM market_student_hypotheses "
                    "ORDER BY created_at ASC LIMIT 100000"
                )
                hypotheses = cur.fetchall() or []
                cur.execute(
                    "SELECT outcome_id, hypothesis_id, status, normalized, resolved_at "
                    "FROM market_student_outcomes ORDER BY resolved_at ASC LIMIT 100000"
                )
                outcomes = cur.fetchall() or []
                cur.execute(
                    "SELECT DISTINCT ON (instrument, mode) instrument, mode, state, "
                    "payload, updated_at "
                    "FROM market_student_thesis_states "
                    "ORDER BY instrument, mode, updated_at DESC LIMIT 100"
                )
                theses = cur.fetchall() or []
                cur.execute(
                    "SELECT reconciliation_id, source_system, source_record_id, "
                    "hypothesis_id, instrument, mode, provenance, matched "
                    "FROM market_student_reconciliations "
                    "ORDER BY created_at ASC LIMIT 100000"
                )
                reconciliations = cur.fetchall() or []
            with self._lock:
                for row in observations:
                    self._observations[str(row[0])] = {
                        "observation_id": row[0], "instrument": row[1], "mode": row[2],
                        "source_system": row[3], "source_event_id": row[4],
                        "source_timestamp": _iso(row[5]), "fingerprint": row[6],
                        "payload": row[7] or {},
                    }
                for row in hypotheses:
                    observation = self._observations.get(str(row[1])) or {}
                    self._hypotheses[str(row[0])] = {
                        "hypothesis_id": row[0], "observation_id": row[1],
                        "instrument": row[2], "mode": row[3],
                        "contract": row[4] or {}, "created_at": _iso(row[5]),
                        "source_system": observation.get("source_system"),
                        "source_event_id": observation.get("source_event_id"),
                    }
                    if observation:
                        observation["hypothesis_id"] = row[0]
                for row in outcomes:
                    self._outcomes[str(row[0])] = {
                        "outcome_id": row[0], "hypothesis_id": row[1],
                        "status": row[2], "normalized": row[3] or {},
                        "resolved_at": _iso(row[4]),
                    }
                for row in theses:
                    payload = row[3] if isinstance(row[3], dict) else {}
                    self._theses[(str(row[0]), str(row[1]))] = {
                        "instrument": row[0], "mode": row[1], "state": row[2],
                        **payload,
                        "updated_at": _iso(row[4]),
                        "restored": True,
                    }
                for row in reconciliations:
                    self._reconciliation.append({
                        "reconciliation_id": row[0],
                        "source_system": row[1],
                        "source_record_id": row[2],
                        "hypothesis_id": row[3],
                        "instrument": row[4],
                        "mode": row[5],
                        "provenance": row[6] or {},
                        "matched": bool(row[7]),
                    })
                self._stats["reconciliation_matched"] = sum(
                    bool(row[7]) for row in reconciliations
                )
            self._stats["restored"] += (
                len(observations) + len(hypotheses) + len(outcomes) +
                len(theses) + len(reconciliations)
            )
            return {
                "observations": len(observations),
                "hypotheses": len(hypotheses),
                "outcomes": len(outcomes),
                "theses": len(theses),
                "reconciliations": len(reconciliations),
            }
        except Exception as exc:
            with self._lock:
                self.persistence_enabled = False
                self._last_error = f"restore: {type(exc).__name__}"
            logger.warning("market student restore unavailable: %s", type(exc).__name__)
            return {"observations": 0, "hypotheses": 0, "outcomes": 0}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _write(self, statements: list[tuple[str, tuple[Any, ...]]]) -> bool:
        """Queue durable writes so analysis callers never wait on Postgres."""
        if not self.persistence_enabled or not self.db_conn_fn:
            return False
        try:
            self._write_queue.put_nowait(statements)
            with self._lock:
                self._stats["writes_queued"] += len(statements)
            return True
        except queue.Full:
            with self._lock:
                self._stats["write_queue_drops"] += len(statements)
                self._last_error = "write_queue_full"
            return False

    def _writer_loop(self) -> None:
        while True:
            statements = self._write_queue.get()
            try:
                self._write_now(statements)
            finally:
                self._write_queue.task_done()

    def _write_now(self, statements: list[tuple[str, tuple[Any, ...]]]) -> bool:
        if not self.persistence_enabled or not self.db_conn_fn:
            return False
        conn = None
        try:
            conn = self.db_conn_fn()
            if conn is None:
                raise RuntimeError("database unavailable")
            with conn.cursor() as cur:
                for sql, params in statements:
                    cur.execute(sql, params)
            conn.commit()
            self._stats["writes"] += len(statements)
            return True
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            with self._lock:
                self._last_error = f"write: {type(exc).__name__}"
                self._stats["write_errors"] += 1
            logger.debug("market student write skipped: %s", type(exc).__name__)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def observe(
        self,
        result: Mapping[str, Any],
        instrument: str,
        mode: str,
        *,
        source_timestamp: Any = None,
        source_system: str = "full_analysis",
        source_event_id: Any = None,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if not self.enabled or not isinstance(result, Mapping):
            return None
        instrument = str(instrument or "").strip().upper()
        mode = canonical_mode(mode)
        if not instrument:
            return None
        contract = build_forecast_contract(
            result, instrument, mode, source_timestamp=source_timestamp,
            source_system=source_system,
        )
        payload = contract.as_dict()
        fingerprint = _hash("fp", {
            "instrument": instrument, "mode": mode, "verdict": contract.deterministic_verdict,
            "direction": contract.direction, "entry": _plan(result).get("entry"),
            "stop": contract.invalidation_price, "targets": contract.targets,
            "reason": result.get("strict_reason"),
        })
        watermark = contract.data_watermark
        key = (instrument, mode)
        now_mono = time.monotonic()
        with self._lock:
            prior = self._last_wait.get(key)
            if not force and is_wait(contract.deterministic_verdict) and prior:
                prior_fp, prior_at = prior
                if prior_fp == fingerprint and now_mono - prior_at < self.wait_heartbeat_seconds:
                    self._stats["wait_heartbeats_suppressed"] += 1
                    return None
            if is_wait(contract.deterministic_verdict):
                self._last_wait[key] = (fingerprint, now_mono)
            observation_id = _hash("mso", {
                "instrument": instrument, "mode": mode,
                "source_system": source_system,
                "source_event_id": source_event_id or watermark,
                "fingerprint": fingerprint,
            })
            if observation_id in self._observations:
                self._stats["duplicate_observations"] += 1
                return None
            hypothesis_id = _hash("msh", {
                "observation_id": observation_id, "contract": payload,
            })
            record = {
                "observation_id": observation_id, "hypothesis_id": hypothesis_id,
                "instrument": instrument, "mode": mode, "source_system": source_system,
                "source_event_id": str(source_event_id or watermark),
                "source_timestamp": watermark, "fingerprint": fingerprint,
                "payload": _jsonable(result.get("market_student_context") or {}),
                "contract": payload, "created_at": _now().isoformat(),
            }
            self._observations[observation_id] = record
            self._hypotheses[hypothesis_id] = record
            self._stats["observations"] += 1
            self._stats["hypotheses"] += 1
        self._write([
            (
                """INSERT INTO market_student_observations
                   (observation_id,instrument,mode,source_system,source_event_id,
                    source_timestamp,fingerprint,payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (observation_id) DO NOTHING""",
                (observation_id, instrument, mode, source_system,
                 str(source_event_id or watermark), watermark, fingerprint, _dump(record["payload"])),
            ),
            (
                """INSERT INTO market_student_hypotheses
                   (hypothesis_id,observation_id,instrument,mode,contract)
                   VALUES (%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (hypothesis_id) DO NOTHING""",
                (hypothesis_id, observation_id, instrument, mode, _dump(payload)),
            ),
        ])
        self._update_thesis(instrument, mode, contract)
        return dict(record)

    def _update_thesis(self, instrument: str, mode: str, contract: ForecastContract) -> None:
        """Research-only hysteresis; it never changes the source verdict."""
        key = (instrument, mode)
        direction = contract.direction
        confidence = contract.confidence or 0.0
        with self._lock:
            previous = dict(self._theses.get(key) or {})
            old_direction = previous.get("direction", "NEUTRAL")
            state = previous.get("state", "NEUTRAL")
            if direction != old_direction and old_direction != "NEUTRAL" and direction != "NEUTRAL":
                # A reversal must first observe a neutral state. This prevents
                # research narration from oscillating on one transient tick.
                state = "NEUTRAL_PENDING_REVERSAL"
            elif direction == "NEUTRAL":
                state = "NEUTRAL"
            elif confidence >= 70:
                state = f"{direction}_CONFIRMED"
            else:
                state = f"{direction}_FORMING"
            self._theses[key] = {
                "instrument": instrument, "mode": mode, "state": state,
                "direction": direction, "confidence": confidence,
                "deterministic_verdict": contract.deterministic_verdict,
                "updated_at": _now().isoformat(),
                "previous_state": previous.get("state"),
            }
            self._stats["thesis_updates"] += 1
            thesis = dict(self._theses[key])
        thesis_id = _hash("mst", {"instrument": instrument, "mode": mode})
        self._write([(
            """INSERT INTO market_student_thesis_states
               (thesis_id,instrument,mode,state,payload,updated_at)
               VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
               ON CONFLICT (instrument,mode) DO UPDATE SET
                 state=EXCLUDED.state,payload=EXCLUDED.payload,updated_at=NOW()""",
            (thesis_id, instrument, mode, state, _dump(thesis)),
        )])

    def record_outcome(
        self,
        hypothesis_id: str,
        *,
        status: str,
        direction: Any = None,
        entry: Any = None,
        stop: Any = None,
        exit_price: Any = None,
        gross_r: Any = None,
        cost_r: Any = None,
        net_r: Any = None,
        reason: str | None = None,
        resolved_at: Any = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        status = str(status or "AMBIGUOUS").upper()
        with self._lock:
            hypothesis = self._hypotheses.get(str(hypothesis_id))
            if hypothesis is None:
                self._stats["unmatched_outcomes"] += 1
                return None
            normalized = normalize_r(
                direction=direction or (hypothesis.get("contract") or {}).get("direction"),
                entry=entry if entry is not None else (hypothesis.get("contract") or {}).get("entry_price"),
                stop=stop if stop is not None else (hypothesis.get("contract") or {}).get("invalidation_price"),
                exit_price=exit_price, gross_r=gross_r, cost_r=cost_r, net_r=net_r,
            )
            outcome_id = _hash("mso", {
                "hypothesis_id": hypothesis_id, "status": status,
                "reason": reason, "normalized": normalized,
            })
            if outcome_id in self._outcomes:
                self._stats["duplicate_outcomes"] += 1
                return None
            resolved_at_iso = _iso(resolved_at) if resolved_at is not None else _now().isoformat()
            record = {
                "outcome_id": outcome_id, "hypothesis_id": str(hypothesis_id),
                "status": status, "reason": reason,
                "normalized": normalized,
                "source_values": {
                    "direction": direction, "entry": entry, "stop": stop,
                    "exit_price": exit_price, "gross_r": gross_r,
                    "cost_r": cost_r, "net_r": net_r,
                },
                "provenance": _jsonable(provenance or {}),
                "resolved_at": resolved_at_iso,
            }
            self._outcomes[outcome_id] = record
            self._stats["outcomes"] += 1
        self._write([
            (
                """INSERT INTO market_student_outcomes
                   (outcome_id,hypothesis_id,status,normalized,source_values,
                     provenance,reason,resolved_at)
                   VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                   ON CONFLICT (outcome_id) DO NOTHING""",
                (outcome_id, str(hypothesis_id), status, _dump(normalized),
                  _dump(record["source_values"]), _dump(record["provenance"]), reason,
                  resolved_at_iso),
            ),
        ])
        return dict(record)

    def record_outcome_by_source(
        self,
        source_system: str,
        source_record_id: str,
        **outcome: Any,
    ) -> dict[str, Any] | None:
        """Resolve by one exact source identity; never time/price correlate."""
        with self._lock:
            matches = [
                row for row in self._observations.values()
                if row.get("source_system") == source_system
                and row.get("source_event_id") == str(source_record_id)
            ]
            if len(matches) != 1:
                self._stats["unmatched_outcomes"] += 1
                return None
            hypothesis_id = matches[0].get("hypothesis_id")
        return self.record_outcome(str(hypothesis_id), **outcome)

    def reconcile(
        self,
        *,
        source_system: str,
        source_record_id: str,
        hypothesis_id: str,
        instrument: str,
        mode: str,
        provenance: Mapping[str, Any] | None = None,
    ) -> bool:
        """Link only exact IDs; no time/price/name fuzzy matching."""
        exact = all(str(v or "").strip() for v in (
            source_system, source_record_id, hypothesis_id, instrument, mode,
        ))
        if not exact or canonical_mode(mode) != str(mode).upper():
            with self._lock:
                self._stats["reconciliation_unmatched"] += 1
            return False
        with self._lock:
            hypothesis = self._hypotheses.get(str(hypothesis_id))
            if not hypothesis or hypothesis.get("instrument") != str(instrument).upper() or hypothesis.get("mode") != str(mode).upper():
                self._stats["reconciliation_unmatched"] += 1
                return False
            identity = _hash("msr", {
                "source_system": source_system, "source_record_id": source_record_id,
                "hypothesis_id": hypothesis_id,
            })
            row = {
                "reconciliation_id": identity, "source_system": source_system,
                "source_record_id": source_record_id, "hypothesis_id": hypothesis_id,
                "instrument": str(instrument).upper(), "mode": str(mode).upper(),
                "provenance": _jsonable(provenance or {}), "matched": True,
            }
            if any(x.get("reconciliation_id") == identity for x in self._reconciliation):
                self._stats["reconciliation_duplicates"] += 1
                return True
            self._reconciliation.append(row)
            self._stats["reconciliation_matched"] += 1
        self._write([
            (
                """INSERT INTO market_student_reconciliations
                   (reconciliation_id,source_system,source_record_id,
                    hypothesis_id,instrument,mode,provenance,matched)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (reconciliation_id) DO NOTHING""",
                (identity, source_system, source_record_id, hypothesis_id,
                 str(instrument).upper(), str(mode).upper(),
                 _dump(row["provenance"]), True),
            ),
        ])
        return True

    def meaningful_ready_transition(self, instrument: str, mode: str, record: Mapping[str, Any]) -> bool:
        """Return true once per meaningful READY setup transition."""
        verdict = str((record.get("contract") or {}).get("deterministic_verdict") or "")
        if is_wait(verdict):
            return False
        key = _hash("alert", {
            "instrument": str(instrument).upper(), "mode": canonical_mode(mode),
            "direction": (record.get("contract") or {}).get("direction"),
            "fingerprint": record.get("fingerprint"),
        })
        with self._lock:
            previous = self._last_alert.get(str(instrument).upper())
            if previous and previous.get("key") == key:
                self._stats["alert_duplicates"] += 1
                return False
            self._last_alert[str(instrument).upper()] = {
                "key": key, "at": _now().isoformat(), "mode": canonical_mode(mode),
            }
            self._stats["alerts"] += 1
            return True

    def claim_ready_alert(
        self,
        instrument: str,
        mode: str,
        record: Mapping[str, Any],
    ) -> str | None:
        """Atomically claim one READY notification in Postgres before delivery.

        A claim is deliberately synchronous: an alert is never sent unless its
        durable dedupe row committed first. Failed deliveries remain claimed and
        visible for operator review; they are not automatically retried.
        """
        verdict = str((record.get("contract") or {}).get("deterministic_verdict") or "")
        hypothesis_id = str(record.get("hypothesis_id") or "")
        if is_wait(verdict) or not hypothesis_id:
            return None
        dedupe_key = _hash("alert", {
            "instrument": str(instrument).upper(),
            "mode": canonical_mode(mode),
            "direction": (record.get("contract") or {}).get("direction"),
            "fingerprint": record.get("fingerprint"),
        })
        if not self.persistence_enabled or not self.db_conn_fn:
            with self._lock:
                self._stats["alert_claim_unavailable"] += 1
                self._last_error = "ready_alert_claim_persistence_unavailable"
            return None
        conn = None
        try:
            conn = self.db_conn_fn()
            if conn is None:
                raise RuntimeError("database unavailable")
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO market_student_ready_alerts
                       (dedupe_key,instrument,mode,hypothesis_id,delivered)
                       VALUES (%s,%s,%s,%s,FALSE)
                       ON CONFLICT (dedupe_key) DO NOTHING
                       RETURNING dedupe_key""",
                    (dedupe_key, str(instrument).upper(), canonical_mode(mode), hypothesis_id),
                )
                claimed = cur.fetchone()
            conn.commit()
            with self._lock:
                if claimed:
                    self._stats["alert_claims"] += 1
                else:
                    self._stats["alert_duplicates"] += 1
            return dedupe_key if claimed else None
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            with self._lock:
                self._stats["alert_claim_errors"] += 1
                self._last_error = f"ready_alert_claim: {type(exc).__name__}"
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def health(self) -> dict[str, Any]:
        now = _now()
        with self._lock:
            observations = list(self._observations.values())
            outcomes = list(self._outcomes.values())
            unresolved = len(self._hypotheses) - len({
                x.get("hypothesis_id") for x in outcomes
            })
            net_values = [
                _safe_float((x.get("normalized") or {}).get("normalized_net_r"))
                for x in outcomes
            ]
            net_values = [x for x in net_values if x is not None]
            calibrated = []
            for outcome in outcomes:
                hypothesis = self._hypotheses.get(str(outcome.get("hypothesis_id"))) or {}
                probability = _safe_float((hypothesis.get("contract") or {}).get("probability"))
                if probability is None:
                    continue
                probability = probability / 100.0 if probability > 1 else probability
                actual = 1.0 if outcome.get("status") == "WIN" else 0.0
                if outcome.get("status") in {"WIN", "LOSS"}:
                    calibrated.append((probability - actual) ** 2)
            ages = []
            for row in observations:
                try:
                    dt = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
                    ages.append(max(0, int((now - dt).total_seconds())))
                except Exception:
                    pass
            return {
                "ok": True,
                "enabled": self.enabled,
                "persistence_enabled": self.persistence_enabled,
                "model_version": self.model_version,
                "ledger_version": LEDGER_VERSION,
                "observation_count": len(observations),
                "hypothesis_count": len(self._hypotheses),
                "outcome_count": len(outcomes),
                "unresolved_hypotheses": max(0, unresolved),
                "canonical_link_rate": (
                    round(self._stats["reconciliation_matched"] /
                          max(1, self._stats["reconciliation_matched"] +
                              self._stats["reconciliation_unmatched"]), 4)
                ),
                "reconciliation": {
                    "matched": self._stats["reconciliation_matched"],
                    "unmatched": self._stats["reconciliation_unmatched"],
                    "duplicates": self._stats["reconciliation_duplicates"],
                },
                "freshness": {
                    "last_observation_age_sec": min(ages) if ages else None,
                    "research_age_sec": max(ages) if ages else None,
                },
                "outcomes": {
                    "wins": sum(x.get("status") == "WIN" for x in outcomes),
                    "losses": sum(x.get("status") == "LOSS" for x in outcomes),
                    "ambiguous": sum(x.get("status") == "AMBIGUOUS" for x in outcomes),
                    "expired": sum(x.get("status") == "EXPIRED" for x in outcomes),
                    "recent_expectancy_r": round(sum(net_values) / len(net_values), 4) if net_values else None,
                },
                "calibration": {
                    "sample_count": len(calibrated),
                    "brier_score": round(sum(calibrated) / len(calibrated), 4) if calibrated else None,
                },
                "sample_sufficiency": {
                    mode: sum(x.get("mode") == mode for x in self._hypotheses.values())
                    for mode in MODES
                },
                "theses": list(self._theses.values()),
                "stats": dict(self._stats),
                "last_error": self._last_error,
                "write_queue_depth": self._write_queue.qsize(),
            }

    def strategy_lab_report(self, min_closed_sample: int = 30) -> dict[str, Any]:
        """Validated canonical-evidence view with a hard manual promotion firewall."""
        with self._lock:
            groups: dict[tuple[str, str], dict[str, Any]] = {}
            outcomes_by_hypothesis = {
                str(row.get("hypothesis_id")): row for row in self._outcomes.values()
            }
            reconciled_hypothesis_ids = {
                str(row.get("hypothesis_id"))
                for row in self._reconciliation
                if row.get("matched") and row.get("hypothesis_id")
            }
            for hypothesis_id, row in self._hypotheses.items():
                contract = row.get("contract") or {}
                strategy = str(contract.get("strategy") or "UNKNOWN")
                mode = str(row.get("mode") or contract.get("mode") or "SCALP")
                group = groups.setdefault((mode, strategy), {
                    "mode": mode, "strategy": strategy, "closed": 0,
                    "wins": 0, "losses": 0, "net_r": 0.0, "samples": [],
                    "canonical": 0, "legacy_only": 0,
                })
                outcome = outcomes_by_hypothesis.get(str(hypothesis_id))
                if not outcome or outcome.get("status") not in {"WIN", "LOSS"}:
                    continue
                group["closed"] += 1
                group["wins"] += int(outcome.get("status") == "WIN")
                group["losses"] += int(outcome.get("status") == "LOSS")
                value = _safe_float((outcome.get("normalized") or {}).get("normalized_net_r"))
                if value is not None:
                    group["net_r"] += value
                probability = _safe_float(contract.get("probability"))
                if probability is not None and probability > 1:
                    probability /= 100.0
                source_system = str(row.get("source_system") or "")
                reconciled = str(hypothesis_id) in reconciled_hypothesis_ids
                group["canonical" if reconciled else "legacy_only"] += 1
                group["samples"].append({
                    "net_r": value,
                    "win": outcome.get("status") == "WIN",
                    "probability": probability,
                    "resolved_at": outcome.get("resolved_at"),
                    "source_system": source_system,
                    "reconciled": reconciled,
                })
            rows = []
            for group in groups.values():
                closed = group["closed"]
                expectancy = group["net_r"] / closed if closed else None
                samples = sorted(group.pop("samples"), key=lambda x: str(x.get("resolved_at") or ""))
                split = max(1, int(len(samples) * 0.7)) if samples else 0
                oos = samples[split:] if len(samples) >= 4 else []
                recent = samples[-20:]
                calibration = [
                    (float(x["probability"]) - (1.0 if x["win"] else 0.0)) ** 2
                    for x in samples if x.get("probability") is not None
                ]
                equity = peak = drawdown = 0.0
                for sample in samples:
                    if sample.get("net_r") is None:
                        continue
                    equity += float(sample["net_r"])
                    peak = max(peak, equity)
                    drawdown = max(drawdown, peak - equity)
                oos_expectancy = (
                    sum(float(x["net_r"]) for x in oos if x.get("net_r") is not None) /
                    max(1, sum(x.get("net_r") is not None for x in oos))
                ) if oos else None
                recent_expectancy = (
                    sum(float(x["net_r"]) for x in recent if x.get("net_r") is not None) /
                    max(1, sum(x.get("net_r") is not None for x in recent))
                ) if recent else None
                guards = {
                    "minimum_closed_sample": closed >= min_closed_sample,
                    "out_of_sample_evidence": bool(oos),
                    "calibration_available": bool(calibration),
                    "positive_expectancy": expectancy is not None and expectancy > 0,
                    "drawdown_reviewed": bool(samples),
                    "manual_review_required": True,
                }
                rows.append({
                    **group,
                    "net_r": round(group["net_r"], 4),
                    "expectancy_r": round(expectancy, 4) if expectancy is not None else None,
                    "recent_expectancy_r": round(recent_expectancy, 4) if recent_expectancy is not None else None,
                    "out_of_sample": {
                        "sample_count": len(oos),
                        "expectancy_r": round(oos_expectancy, 4) if oos_expectancy is not None else None,
                    },
                    "calibration": {
                        "sample_count": len(calibration),
                        "brier_score": round(sum(calibration) / len(calibration), 4) if calibration else None,
                    },
                    "max_drawdown_r": round(drawdown, 4),
                    "evidence": {
                        "canonical_reconciled": group["canonical"],
                        "legacy_unvalidated": group["legacy_only"],
                        "complete_canonical": group["legacy_only"] == 0 and group["canonical"] == closed,
                    },
                    "promotion_eligible": False,
                    "promotion_guards": guards,
                })
            rows.sort(key=lambda row: (-row["closed"], row["mode"], row["strategy"]))
            return {
                "ok": True,
                "evidence_source": "market_student_exact_terminal_outcomes",
                "validated_canonical_only": True,
                "automatic_promotion": False,
                "minimum_closed_sample": min_closed_sample,
                "strategies": rows,
            }

    def alert_health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "alerts_emitted": int(self._stats["alerts"]),
                "claims_committed": int(self._stats["alert_claims"]),
                "claim_errors": int(self._stats["alert_claim_errors"]),
                "claim_unavailable": int(self._stats["alert_claim_unavailable"]),
                "duplicate_alerts_suppressed": int(self._stats["alert_duplicates"]),
                "delivery_errors": int(self._stats["alert_delivery_errors"]),
                "last_delivery_at": self._stats.get("last_alert_delivery_at"),
            }

    def record_alert_delivery(
        self,
        delivered: bool,
        *,
        dedupe_key: str | None = None,
        error: str | None = None,
    ) -> None:
        if dedupe_key and self.persistence_enabled:
            self._write([(
                """UPDATE market_student_ready_alerts
                   SET delivered=%s, delivery_error=%s,
                       delivered_at=CASE WHEN %s THEN NOW() ELSE delivered_at END
                   WHERE dedupe_key=%s""",
                (bool(delivered), None if delivered else str(error or "delivery_failed")[:200],
                 bool(delivered), dedupe_key),
            )])
        with self._lock:
            if delivered:
                self._stats["alert_deliveries"] += 1
                self._stats["last_alert_delivery_at"] = _now().isoformat()
            else:
                self._stats["alert_delivery_errors"] += 1
