"""P4 regression coverage for the append-only final-verdict observer."""

from __future__ import annotations

import copy
import queue
import threading
import time
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


def test_snapshot_preserves_strict_blockers_separately_from_final_vetoes():
    snap = avh._build_snapshot(
        _result(
            verdict="WAIT", score=96, strict_missing=[],
            strict_blockers=[],
            final_veto_reasons=[
                {
                    "stage": "scalp_quality",
                    "code": "room",
                    "reason": "only 0.8R room to the opposing zone (need 1.25R)",
                },
                {
                    "stage": "scalp_quality",
                    "code": "opposing_zone",
                    "reason": "price entering the opposing zone",
                },
            ],
        ),
        "MNQ", "SCALP", {},
    )

    # Legacy blockers stay exactly where existing history readers expect them;
    # the new final-veto diagnostics live in the immutable JSON payload only.
    assert snap["strict_blockers"] == []
    assert [item["code"] for item in snap["final_veto_reasons"]] == [
        "room", "opposing_zone",
    ]
    assert snap["score"] == 96
    assert snap["safety"]["execution_enabled"] is False


def test_snapshot_uses_explicit_native_source_timestamp_not_signal_wall_clock():
    snap = avh._build_snapshot(
        _result(signal_time="2026-08-24T19:00:00+00:00"),
        "MNQ", "SCALP", {},
        source_timestamp=1_787_599_200,
    )

    assert snap["source_timestamp"] == "2026-08-24T19:20:00+00:00"


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


def test_slow_writer_never_blocks_or_mutates_returned_analysis():
    avh._DB_READY = True
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def slow_connection():
        writer_entered.set()
        assert release_writer.wait(1)
        return _mock_connection()[0]

    original = _result()
    before = copy.deepcopy(original)
    avh.configure(slow_connection)

    started = time.monotonic()
    assert avh.observe(original, "MNQ", "SCALP", {}) is True
    elapsed = time.monotonic() - started

    assert elapsed < 0.05
    assert original == before
    assert writer_entered.wait(1)
    release_writer.set()
    assert avh._WORK_QUEUE.join() is None


def test_first_write_failure_retries_once_then_persists_unchanged_event():
    avh._DB_READY = True
    attempts = []
    persisted = threading.Event()

    def fail_then_succeed(event):
        attempts.append(copy.deepcopy(event))
        if len(attempts) == 2:
            persisted.set()
            return True
        return False

    with patch.object(avh, "_persist_event", side_effect=fail_then_succeed):
        assert avh.observe(_result(), "MNQ", "SCALP", {}) is True
        assert persisted.wait(1)
        assert avh._WORK_QUEUE.join() is None

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert attempts[0]["payload"]["source_timestamp"] is None
    scope = ("MNQ", "SCALP")
    assert avh._LAST_BY_SCOPE[scope][0] == attempts[0]["observation_key"]


def test_failed_head_cancels_queued_descendant_and_restarts_valid_chain():
    avh._DB_READY = True
    first_attempt = threading.Event()
    allow_failure = threading.Event()
    attempts = []

    def fail_head_then_persist_recovery(event):
        attempts.append(copy.deepcopy(event))
        if event["score"] == 78:
            first_attempt.set()
            assert allow_failure.wait(1)
            return False
        return True

    successor = _result(score=79)
    with patch.object(avh, "_persist_event", side_effect=fail_head_then_persist_recovery):
        assert avh.observe(_result(score=78), "MNQ", "SCALP", {}) is True
        assert first_attempt.wait(1)
        # This event chains to the pending head at admission time.  Once that
        # head exhausts its retries, it must be cancelled rather than inserted
        # with a missing previous_observation_key.
        assert avh.observe(successor, "MNQ", "SCALP", {}) is True
        allow_failure.set()
        assert avh._WORK_QUEUE.join() is None

        scope = ("MNQ", "SCALP")
        assert avh._LAST_BY_SCOPE[scope] == ("", "")
        assert [event["score"] for event in attempts] == [78, 78, 78]

        # The next analysis can retry the successor as a new root observation.
        assert avh.observe(successor, "MNQ", "SCALP", {}) is True
        assert avh._WORK_QUEUE.join() is None

    assert attempts[-1]["score"] == 79
    assert attempts[-1]["previous_observation_key"] is None


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


def test_operator_history_report_exposes_curated_chain_continuity():
    rows = []
    for event_id, key, previous, verdict, score, blockers in (
        (1, "root-key", None, "WAIT", 42, ["structure_confirmed"]),
        (2, "ready-key", "root-key", "LONG READY", 86, []),
        (3, "broken-key", "missing-key", "WAIT", 61, ["vwap_confirmed"]),
    ):
        row = [None] * len(avh._HISTORY_COLUMNS)
        values = {
            "event_id": event_id,
            "observation_key": key,
            "previous_observation_key": previous,
            "instrument": "MNQ",
            "mode": "SCALP",
            "verdict": verdict,
            "wait_ready_state": "READY" if "READY" in verdict else "WAIT",
            "actionable": "READY" in verdict,
            "blocked": bool(blockers),
            "score": score,
            "grade": "A+" if score >= 85 else "B",
            "blockers": blockers,
            "waiting_for": blockers,
            "recorded_at": f"2026-08-28T04:0{event_id}:00+00:00",
            "payload": {"must_not": "leak"},
        }
        for name, value in values.items():
            row[avh._HISTORY_COLUMNS.index(name)] = value
        rows.append(tuple(row))

    conn, _ = _mock_connection(rows=rows)
    avh._DB_READY = True
    avh.configure(lambda: conn)

    report = avh.get_history_report("mnq", "scalp", limit=120)

    assert report["ok"] is True
    assert report["available"] is True
    assert report["read_only"] is True
    assert report["observer_only"] is True
    assert report["chain"] == {
        "status": "BROKEN", "roots": 1, "contiguous": 1, "breaks": 1,
        "partial": False,
    }
    assert [event["chain_status"] for event in report["events"]] == [
        "ROOT", "CONTIGUOUS", "BROKEN",
    ]
    assert report["events"][2]["chain_expected_previous"] == "ready-key"
    assert "payload" not in report["events"][0]


def test_operator_history_report_distinguishes_empty_from_unavailable():
    avh._DB_READY = False
    unavailable = avh.get_history_report("MGC", "INTRADAY_TREND")
    assert unavailable["available"] is False
    assert unavailable["error"] == "history_unavailable"

    conn, _ = _mock_connection(rows=[])
    avh._DB_READY = True
    avh.configure(lambda: conn)
    empty = avh.get_history_report("MGC", "INTRADAY_TREND")
    assert empty["ok"] is True
    assert empty["available"] is True
    assert empty["chain"]["status"] == "EMPTY"
    assert empty["chain"]["partial"] is False
    assert empty["events"] == []


def test_operator_history_report_marks_a_bounded_chain_window_as_partial():
    rows = []
    for event_id, key, previous in (
        (7, "outside-window", "older-key"),
        (8, "window-first", "outside-window"),
        (9, "window-second", "window-first"),
    ):
        row = [None] * len(avh._HISTORY_COLUMNS)
        values = {
            "event_id": event_id,
            "observation_key": key,
            "previous_observation_key": previous,
            "instrument": "MGC",
            "mode": "SCALP",
            "verdict": "WAIT",
            "wait_ready_state": "WAIT",
            "recorded_at": f"2026-08-28T04:0{event_id}:00+00:00",
        }
        for name, value in values.items():
            row[avh._HISTORY_COLUMNS.index(name)] = value
        rows.append(tuple(row))

    conn, cursor = _mock_connection(rows=rows)
    avh._DB_READY = True
    avh.configure(lambda: conn)

    report = avh.get_history_report("MGC", "SCALP", limit=2)

    assert report["chain"] == {
        "status": "PARTIAL", "roots": 0, "contiguous": 1, "breaks": 0,
        "partial": True,
    }
    assert report["events"][0]["chain_status"] == "WINDOW_START"
    sql = cursor.execute.call_args.args[0]
    assert "ORDER BY event_id DESC" in sql
    assert "ORDER BY event_id ASC" in sql


def test_operator_history_report_does_not_mask_a_missing_predecessor_as_partial():
    row = [None] * len(avh._HISTORY_COLUMNS)
    values = {
        "event_id": 1,
        "observation_key": "orphan",
        "previous_observation_key": "missing",
        "instrument": "MYM",
        "mode": "INTRADAY_TREND",
        "verdict": "WAIT",
        "wait_ready_state": "WAIT",
        "recorded_at": "2026-08-28T04:01:00+00:00",
    }
    for name, value in values.items():
        row[avh._HISTORY_COLUMNS.index(name)] = value
    conn, _ = _mock_connection(rows=[tuple(row)])
    avh._DB_READY = True
    avh.configure(lambda: conn)

    report = avh.get_history_report("MYM", "INTRADAY_TREND", limit=50)

    assert report["chain"]["status"] == "BROKEN"
    assert report["chain"]["partial"] is False
    assert report["chain"]["breaks"] == 1
    assert report["events"][0]["chain_status"] == "BROKEN"


def test_operator_history_report_uses_append_order_when_timestamps_invert():
    rows = []
    for event_id, key, previous, recorded_at in (
        (1, "root", None, "2026-08-28T04:02:00+00:00"),
        (2, "child", "root", "2026-08-28T04:01:00+00:00"),
    ):
        row = [None] * len(avh._HISTORY_COLUMNS)
        values = {
            "event_id": event_id,
            "observation_key": key,
            "previous_observation_key": previous,
            "instrument": "MNQ",
            "mode": "SCALP",
            "verdict": "WAIT",
            "wait_ready_state": "WAIT",
            "recorded_at": recorded_at,
        }
        for name, value in values.items():
            row[avh._HISTORY_COLUMNS.index(name)] = value
        rows.append(tuple(row))

    conn, cursor = _mock_connection(rows=rows)
    avh._DB_READY = True
    avh.configure(lambda: conn)

    report = avh.get_history_report("MNQ", "SCALP", limit=50)

    assert report["chain"]["status"] == "VALID"
    assert [event["observation_key"] for event in report["events"]] == [
        "root", "child",
    ]
    sql = cursor.execute.call_args.args[0]
    assert "ORDER BY event_id DESC" in sql
    assert "ORDER BY event_id ASC" in sql


def _structure_row(at, *, recorded_at=None, state="REVERSAL_CANDIDATE",
                    blocked=True, score=88, event="BOS continuation",
                    actionable=False, direction="Long", **extra):
    return {
        "instrument": "MNQ",
        "mode": "SCALP",
        "candidate_direction": direction,
        "actionable_direction": direction if actionable else None,
        "actionable": actionable,
        "blocked": blocked,
        "score": score,
        "grade": "A+",
        "blockers": ["structure_confirmed"] if blocked else [],
        "waiting_for": [event] if blocked else [],
        "waiting_for_guidance": "Waiting for structure confirmation" if blocked else None,
        "structure_cycle_state": state,
        "structure_next_event": event,
        "structure_context": {"cycle_confirmed": state == "TREND_CONTINUATION"},
        "source_timestamp": at,
        "recorded_at": recorded_at or at,
        **extra,
    }


def test_structure_confirmation_diagnostic_classifies_continuation_and_exposes_event():
    rows = [
        _structure_row("2026-08-25T14:00:00+00:00", event="BOS continuation"),
        _structure_row(
            "2026-08-25T14:02:00+00:00", state="TREND_CONTINUATION",
            blocked=False, actionable=True,
        ),
    ]
    report = avh.build_structure_confirmation_diagnostic(rows, now="2026-08-25T14:03:00+00:00")
    assert report["counts"]["CONFIRMED_CONTINUATION"] == 1
    case = report["cases"][0]
    assert case["elapsed_seconds"] == 120
    assert case["outstanding_event"] == "BOS continuation"
    assert case["source_timestamp"] == "2026-08-25T14:00:00+00:00"


def test_structure_confirmation_diagnostic_separates_expiry_and_detector_no_update():
    rows = [
        _structure_row("2026-08-25T14:00:00+00:00"),
        _structure_row("2026-08-25T14:07:00+00:00"),
    ]
    report = avh.build_structure_confirmation_diagnostic(
        rows, now="2026-08-25T14:11:00+00:00",
        confirmation_window_seconds=600, detector_no_update_seconds=900,
    )
    assert report["counts"]["EXPIRED"] == 1

    report = avh.build_structure_confirmation_diagnostic(
        [_structure_row("2026-08-25T14:00:00+00:00")],
        now="2026-08-25T14:05:00+00:00",
        confirmation_window_seconds=900, detector_no_update_seconds=300,
    )
    assert report["counts"]["DETECTOR_NO_UPDATE"] == 1


def test_structure_confirmation_diagnostic_identifies_source_data_delay():
    report = avh.build_structure_confirmation_diagnostic(
        [_structure_row(
            "2026-08-25T14:00:00+00:00",
            recorded_at="2026-08-25T14:03:00+00:00",
        )],
        now="2026-08-25T14:04:00+00:00",
        source_delay_seconds=120,
    )
    assert report["counts"]["SOURCE_DATA_DELAY"] == 1
    case = report["cases"][0]
    assert case["source_delay_seconds"] == 180
    assert isinstance(case["started_at"], str)


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
    section = source[hook - 1400:hook + 300]
    assert order_flow < hook < final_return
    assert '("SCALP", "INTRADAY_TREND")' in section
    assert "SWING" in section
    assert "bounded non-blocking queue" in section
    assert "_avh_source_timestamp" in section
    assert "source_timestamp=_avh_source_timestamp" in section