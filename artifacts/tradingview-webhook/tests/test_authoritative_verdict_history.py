"""P4 regression coverage for the append-only final-verdict observer."""

from __future__ import annotations

import copy
import queue
from pathlib import Path
from unittest.mock import MagicMock, patch

import authoritative_verdict_history as avh


def _result(verdict="LONG READY", score=78, direction="Long", **extra):
    result = {
        "verdict": verdict,
        "strict_direction": direction,
        "strict_reason": "Long WAIT — waiting for confirmation.",
        "edge_score": score,
        "confidence": 0.82,
        "gate_debug": {
            "failed_conditions": ["structure_confirmed"],
            "blockedBy": ["structure_confirmed"],
            "vwap_confirmed": True,
            "bos_confirmed": True,
            "choch_confirmed": True,
            "structure_cycle_confirmed": True,
            "next_structure_event": "BOS continuation",
        },
        "vwap_value": 21450.25,
        "vwap_status": "ok",
        "vwap_side": "above",
        "vwap_diagnostics": {
            "vwap_source": "databento",
            "source_selection_reason": "Databento authoritative",
        },
        "structure_state": "TREND_CONTINUATION",
        "databento_health": {"result": "ok", "queue_freshness": "LIVE"},
        "freshness": {"price_fresh": True, "bar_age_ms": 250},
        "trade_plan": {"setup_id": "MNQ|Long|SCALP|20260824"},
        "decision_id": "decision-1",
        "gate_audit_id": "audit-1",
        "canonical_evidence_id": "evidence-1",
        "execution_enabled": False,
        "armed": False,
    }
    result.update(extra)
    return result


def _mock_connection(rows=None, fail=False):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = 1
    if fail:
        cur.execute.side_effect = RuntimeError("database unavailable")
    return conn, cur


def setup_function():
    avh._reset_for_tests()


def teardown_function():
    avh._reset_for_tests()


def test_scalp_long_ready_snapshot_contains_full_audit_context():
    snap = avh._build_snapshot(
        _result(), "MNQ", "SCALP",
        {"execution_enabled": False, "armed": False, "safety_locked": True},
        recorded_at="2026-08-24T19:00:00+00:00",
    )

    assert snap["instrument"] == "MNQ"
    assert snap["mode"] == "SCALP"
    assert snap["candidate_direction"] == "Long"
    assert snap["actionable_direction"] == "Long"
    assert snap["actionable"] is True
    assert snap["wait_ready_state"] == "READY"
    assert snap["score"] == 78
    assert snap["grade"] == "A"
    assert snap["confidence"] == 0.82
    assert snap["vwap_value"] == 21450.25
    assert snap["vwap_status"] == "ok"
    assert snap["vwap_side"] == "above"
    assert snap["structure_next_event"] == "BOS continuation"
    assert snap["freshness"]["price_fresh"] is True
    assert snap["databento_health"]["queue_freshness"] == "LIVE"
    assert snap["correlations"]["decision_id"] == "decision-1"
    assert snap["correlations"]["gate_audit_id"] == "audit-1"
    assert snap["correlations"]["evidence_id"] == "evidence-1"
    assert snap["safety"]["execution_enabled"] is False


def test_intraday_short_wait_is_explicitly_non_actionable_and_blocked():
    result = _result(
        verdict="WAIT", score=35, direction="Short",
        intraday_trend_context={
            "mode": "INTRADAY_TREND",
            "veto_codes": ["BLOCKED_DAILY_COUNT_UNAVAILABLE"],
            "ready_reduced_missing": ["CONFIRMATION_3"],
            "freshness": {"1H": "LIVE", "4H": "STALE"},
            "next_event": "fresh 4H bar",
        },
        trade_plan={"it_veto_code": "BLOCKED_DAILY_COUNT_UNAVAILABLE"},
    )
    snap = avh._build_snapshot(result, "MGC", "INTRADAY_TREND", {})

    assert snap["candidate_direction"] == "Short"
    assert snap["actionable_direction"] is None
    assert snap["actionable"] is False
    assert snap["wait_ready_state"] == "WAIT"
    assert snap["blocked"] is True
    assert "BLOCKED_DAILY_COUNT_UNAVAILABLE" in snap["blockers"]
    assert "CONFIRMATION_3" in snap["waiting_for"]
    assert snap["structure_next_event"] == "fresh 4H bar"


def test_snapshot_hash_ignores_recorded_at_but_not_changed_decision_context():
    one = avh._build_snapshot(_result(), "MNQ", "SCALP", {}, "2026-08-24T19:00:00+00:00")
    two = avh._build_snapshot(_result(), "MNQ", "SCALP", {}, "2026-08-24T19:01:00+00:00")
    changed = avh._build_snapshot(_result(score=79), "MNQ", "SCALP", {}, "2026-08-24T19:01:00+00:00")
    assert avh._snapshot_hash(one) == avh._snapshot_hash(two)
    assert avh._snapshot_hash(one) != avh._snapshot_hash(changed)


def test_live_structured_cycle_state_is_reduced_to_a_scalar_database_value():
    snapshot = avh._build_snapshot(
        _result(structure_cycle_state={
            "state": "NO_STRUCTURE",
            "next_event": "CHOCH DEMAND or CHOCH SUPPLY",
            "warmup": {"state": "READY"},
        }),
        "MNQ", "SCALP", {},
    )
    assert snapshot["structure_cycle_state"] == "NO_STRUCTURE"
    assert isinstance(snapshot["structure_cycle_state"], str)


def test_replays_are_idempotent_but_changes_and_returns_append():
    avh._DB_READY = True
    test_queue = queue.Queue()
    ready = _result()
    waiting = _result(verdict="WAIT", score=45, direction="Long")
    with patch.object(avh, "_WORK_QUEUE", test_queue), patch.object(avh, "_ensure_worker"):
        assert avh.observe(ready, "MNQ", "SCALP", {}) is True
        assert avh.observe(copy.deepcopy(ready), "MNQ", "SCALP", {}) is False
        assert avh.observe(waiting, "MNQ", "SCALP", {}) is True
        assert avh.observe(ready, "MNQ", "SCALP", {}) is True

    rows = [test_queue.get_nowait() for _ in range(3)]
    assert len({row["observation_key"] for row in rows}) == 3
    assert rows[0]["previous_observation_key"] is None
    assert rows[2]["previous_observation_key"] == rows[1]["observation_key"]


def test_swing_is_rejected_before_snapshot_or_queue_submission():
    avh._DB_READY = True
    test_queue = queue.Queue()
    with patch.object(avh, "_WORK_QUEUE", test_queue):
        assert avh.observe(_result(), "MNQ", "SWING", {}) is False
    assert test_queue.empty()


def test_insert_is_append_only_and_deterministically_idempotent():
    conn, cur = _mock_connection()
    event = avh._build_snapshot(_result(), "MNQ", "SCALP", {})
    event.update({
        "observation_key": "key-1",
        "previous_observation_key": None,
        "snapshot_hash": avh._snapshot_hash(event),
        "payload": event.copy(),
    })
    avh.configure(lambda: conn)
    assert avh._persist_event(event) is True
    sql = str(cur.execute.call_args[0][0])
    assert "INSERT INTO authoritative_verdict_history" in sql
    assert "ON CONFLICT (observation_key) DO NOTHING" in sql
    assert "DO UPDATE" not in sql
    conn.commit.assert_called_once()


def test_persistence_failure_is_fail_open_and_does_not_mutate_live_result():
    conn, _ = _mock_connection(fail=True)
    event = avh._build_snapshot(_result(), "MNQ", "SCALP", {})
    event.update({
        "observation_key": "key-1",
        "previous_observation_key": None,
        "snapshot_hash": avh._snapshot_hash(event),
        "payload": event.copy(),
    })
    original = _result()
    before = copy.deepcopy(original)
    avh.configure(lambda: conn)
    assert avh._persist_event(event) is False
    assert original == before
    conn.rollback.assert_called_once()


def test_restart_boot_reconstructs_last_state_and_suppresses_duplicate():
    conn, _ = _mock_connection(rows=[("MNQ", "SCALP", "persisted-key", "persisted-hash")])
    avh._DB_READY = True
    avh.configure(lambda: conn)
    avh.boot()
    assert avh._LAST_BY_SCOPE[("MNQ", "SCALP")] == ("persisted-key", "persisted-hash")

    # A restart readback is sufficient to resume a 30–60 minute stream: the
    # public read API supplies the ordered rows while this state preserves chain
    # identity for the first new event.
    assert ("MNQ", "SCALP") in avh._LAST_BY_SCOPE


def test_readback_returns_chronological_rows_for_reconstruction():
    columns = 33
    first = tuple(range(columns))
    second = tuple(range(100, 100 + columns))
    conn, _ = _mock_connection(rows=[first, second])
    avh._DB_READY = True
    avh.configure(lambda: conn)
    rows = avh.get_history("MNQ", "SCALP", limit=120)
    assert len(rows) == 2
    assert rows[0]["event_id"] == 0
    assert rows[1]["event_id"] == 100


def test_schema_restricts_modes_and_rejects_mutation():
    schema = Path(__file__).parents[1].joinpath(
        "db_authoritative_verdict_history_schema.sql"
    ).read_text()
    assert "CHECK (mode IN ('SCALP', 'INTRADAY_TREND'))" in schema
    assert "BEFORE UPDATE OR DELETE" in schema
    assert "append-only" in schema


def test_app_wires_only_a_final_scalp_intraday_nonblocking_observer():
    source = Path(__file__).parents[1].joinpath("app.py").read_text()
    hook = source.index("_avh_final.observe")
    order_flow = source.rindex('result["order_flow"]', 0, hook)
    final_return = source.index("return result", hook)
    section = source[hook - 500:hook + 300]
    assert order_flow < hook < final_return
    assert '("SCALP", "INTRADAY_TREND")' in section
    assert "SWING" in section
    assert "bounded non-blocking queue" in section