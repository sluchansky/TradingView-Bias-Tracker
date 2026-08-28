"""Focused tests for the read-only production P4 health diagnostic."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import app
import authoritative_verdict_history as avh


class _ReadConnection:
    def __init__(self, batches=None, error=None):
        self.batches = list(batches or [])
        self.error = error
        self.executed = []
        self.closed = False

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql):
                connection.executed.append(sql)
                if connection.error:
                    raise connection.error

            def fetchall(self):
                return connection.batches.pop(0) if connection.batches else []

        return Cursor()

    def close(self):
        self.closed = True


def _observer_status(**overrides):
    status = {
        "db_ready": True,
        "observer_enabled": True,
        "worker_enabled": True,
        "worker_running": True,
        "queue_depth": 2,
        "queue_capacity": 512,
        "dropped_events": 1,
        "retry_attempts": 3,
        "persistence_errors": 1,
        "persistence_error": "database_write_failed",
        "pending_events": 2,
        "written_events": 11,
        "readiness_error": None,
        "last_probe_error": None,
        "last_probe_at": None,
        "last_startup_error": None,
        "last_startup_at": None,
        "queue_saturated": False,
        "recent": {
            "window_seconds": 86400,
            "retry_attempts": 4,
            "persistence_errors": 2,
        },
    }
    status.update(overrides)
    return status


def test_ready_reports_process_worker_and_aggregate_source_timestamp_health(monkeypatch):
    conn = _ReadConnection([
        [
            ("SCALP", 7, 2, 5, datetime(2026, 8, 24, 19, 5, tzinfo=timezone.utc),
             datetime(2026, 8, 24, 19, 4, tzinfo=timezone.utc)),
            ("INTRADAY_TREND", 3, 1, 2, datetime(2026, 8, 24, 19, 6, tzinfo=timezone.utc),
             datetime(2026, 8, 24, 19, 3, tzinfo=timezone.utc)),
        ],
        [
            ("SCALP", 2, 0, 2, datetime(2026, 8, 24, 19, 5, tzinfo=timezone.utc),
             datetime(2026, 8, 24, 19, 4, tzinfo=timezone.utc)),
            ("INTRADAY_TREND", 1, 1, 0, datetime(2026, 8, 24, 19, 6, tzinfo=timezone.utc),
             None),
        ],
    ])
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(app, "_learning_conn", lambda: conn)
    monkeypatch.setattr(avh, "status", lambda: _observer_status())

    response = app.app.test_client().get("/authoritative-verdict-history-health")
    assert response.status_code == 200
    body = response.get_json()

    assert body["health_status"] == "READY"
    assert body["read_only"] is True
    assert body["observer_only"] is True
    assert body["process"] == {
        "database_configured": True,
        "table_ready": True,
        "database_ready": True,
        "readiness_error": None,
        "last_probe_error": None,
        "last_probe_at": None,
        "last_startup_error": None,
        "last_startup_at": None,
    }
    assert body["observer"]["worker_running"] is True
    assert body["observer"]["queue_depth"] == 2
    assert body["observer"]["dropped_events"] == 1
    assert body["observer"]["retry_attempts"] == 3
    assert body["observer"]["recent"] == {
        "window_seconds": 86400,
        "retry_attempts": 4,
        "persistence_errors": 2,
    }
    assert body["persistence"]["newest_recorded_at"] == "2026-08-24T19:06:00+00:00"
    assert body["persistence"]["newest_source_timestamp"] == "2026-08-24T19:04:00+00:00"
    assert body["persistence"]["total_rows"] == 10
    assert body["persistence"]["by_mode"]["SCALP"] == {
        "rows": 7,
        "source_timestamp": {"null": 2, "non_null": 5},
    }
    assert body["persistence"]["by_mode"]["INTRADAY_TREND"] == {
        "rows": 3,
        "source_timestamp": {"null": 1, "non_null": 2},
    }
    assert body["persistence"]["source_timestamp"] == {"null": 3, "non_null": 7}
    assert body["persistence"]["recent"] == {
        "window_hours": 24,
        "rows": 3,
        "by_mode": {
            "SCALP": {
                "rows": 2,
                "source_timestamp": {"null": 0, "non_null": 2},
            },
            "INTRADAY_TREND": {
                "rows": 1,
                "source_timestamp": {"null": 1, "non_null": 0},
            },
        },
        "source_timestamp": {"null": 1, "non_null": 2},
        "newest_recorded_at": "2026-08-24T19:06:00+00:00",
        "newest_source_timestamp": "2026-08-24T19:04:00+00:00",
    }
    assert body["persistence"]["query_error"] is None
    assert "payload" not in body
    assert "database_url" not in body
    assert all("SELECT" in sql.upper() for sql in conn.executed)
    assert conn.closed is True


def test_missing_table_reports_disabled_observer_and_null_counts(monkeypatch):
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", False)
    monkeypatch.setattr(
        avh,
        "status",
        lambda: _observer_status(
            db_ready=False,
            observer_enabled=False,
            worker_enabled=False,
            worker_running=False,
            queue_depth=0,
            dropped_events=0,
            retry_attempts=0,
            persistence_errors=0,
            persistence_error=None,
            pending_events=0,
            readiness_error="table_missing",
        ),
    )
    connection = MagicMock()
    monkeypatch.setattr(app, "_learning_conn", lambda: connection)

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "TABLE_MISSING"
    assert body["process"]["table_ready"] is False
    assert body["process"]["database_ready"] is False
    assert body["process"]["readiness_error"] == "table_missing"
    assert body["persistence"]["total_rows"] is None
    assert body["persistence"]["recent"]["rows"] is None
    connection.assert_not_called()


def test_db_failure_reports_safe_query_error_without_exception_details(monkeypatch):
    conn = _ReadConnection(error=RuntimeError("password=secret database unavailable"))
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(app, "_learning_conn", lambda: conn)
    monkeypatch.setattr(avh, "status", lambda: _observer_status())

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "DB_FAILURE"
    assert body["process"]["database_ready"] is True
    assert body["persistence"]["query_error"] == "database_connection_failed"
    assert "secret" not in str(body).lower()
    assert conn.closed is True


def test_unavailable_db_reports_db_failure_without_querying_or_exposing_details(monkeypatch):
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(avh, "status", lambda: _observer_status())
    monkeypatch.setattr(app, "_learning_conn", lambda: None)

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "DB_FAILURE"
    assert body["process"]["database_ready"] is False
    assert body["process"]["readiness_error"] == "database_connection_unavailable"
    assert body["persistence"]["total_rows"] is None
    assert body["persistence"]["query_error"] is None


def test_empty_table_reports_real_zero_counts_and_null_newest_timestamps(monkeypatch):
    conn = _ReadConnection([[], []])
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(app, "_learning_conn", lambda: conn)
    monkeypatch.setattr(avh, "status", lambda: _observer_status(
        queue_depth=0,
        dropped_events=0,
        persistence_errors=0,
        persistence_error=None,
    ))

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "READY"
    assert body["persistence"]["total_rows"] == 0
    assert body["persistence"]["by_mode"]["SCALP"]["rows"] == 0
    assert body["persistence"]["by_mode"]["INTRADAY_TREND"]["rows"] == 0
    assert body["persistence"]["newest_recorded_at"] is None
    assert body["persistence"]["newest_source_timestamp"] is None
    assert body["persistence"]["recent"]["rows"] == 0


def test_queue_backpressure_state_is_reported_without_touching_persistence(monkeypatch):
    conn = _ReadConnection([[], []])
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(app, "_learning_conn", lambda: conn)
    monkeypatch.setattr(avh, "status", lambda: _observer_status(
        queue_depth=512,
        queue_capacity=512,
        queue_saturated=True,
        dropped_events=9,
        pending_events=512,
        recent={"window_seconds": 86400, "retry_attempts": 6, "persistence_errors": 3},
    ))

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "READY"
    assert body["observer"]["queue_depth"] == 512
    assert body["observer"]["queue_capacity"] == 512
    assert body["observer"]["queue_saturated"] is True
    assert body["observer"]["dropped_events"] == 9
    assert body["observer"]["recent"]["retry_attempts"] == 6
    assert body["observer"]["recent"]["persistence_errors"] == 3


def test_serialization_normalizes_timestamp_values_and_has_no_sensitive_fields(monkeypatch):
    conn = _ReadConnection([[
        ("SCALP", 1, 1, 0, datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc), None),
    ], []])
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", True)
    monkeypatch.setattr(app, "AVH_DB_READY", True)
    monkeypatch.setattr(app, "_learning_conn", lambda: conn)
    monkeypatch.setattr(avh, "status", lambda: _observer_status())

    response = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    )
    body = response.get_json()

    assert response.status_code == 200
    assert json.dumps(body)
    assert body["persistence"]["newest_recorded_at"] == "2026-08-24T20:00:00+00:00"
    serialized = json.dumps(body).lower()
    assert "payload" not in serialized
    assert "database_url" not in serialized
    assert "password" not in serialized
    assert "connection_string" not in serialized


def test_observer_disabled_reports_disabled_state_without_database_access(monkeypatch):
    monkeypatch.setattr(app, "LEARNING_DB_ENABLED", False)
    monkeypatch.setattr(app, "AVH_DB_READY", False)
    monkeypatch.setattr(
        avh,
        "status",
        lambda: _observer_status(
            db_ready=False,
            observer_enabled=False,
            worker_enabled=False,
            worker_running=False,
            readiness_error="database_not_configured",
        ),
    )
    connection = MagicMock()
    monkeypatch.setattr(app, "_learning_conn", lambda: connection)

    body = app.app.test_client().get(
        "/authoritative-verdict-history-health"
    ).get_json()

    assert body["health_status"] == "DISABLED"
    assert body["process"]["database_configured"] is False
    assert body["process"]["readiness_error"] == "database_not_configured"
    assert body["observer"]["enabled"] is False
    assert body["observer"]["worker_running"] is False
    assert body["persistence"]["total_rows"] is None
    connection.assert_not_called()


def test_operator_history_route_validates_scope_and_returns_curated_report(monkeypatch):
    calls = []

    def report(instrument, mode, limit):
        calls.append((instrument, mode, limit))
        return {
            "ok": True,
            "available": True,
            "read_only": True,
            "observer_only": True,
            "instrument": instrument,
            "mode": mode,
            "count": 0,
            "chain": {
                "status": "EMPTY", "roots": 0, "contiguous": 0, "breaks": 0,
                "partial": False,
            },
            "events": [],
        }

    monkeypatch.setattr(avh, "get_history_report", report)
    client = app.app.test_client()

    response = client.get(
        "/authoritative-verdict-history"
        "?instrument=mnq&mode=intraday_trend&limit=500"
    )
    assert response.status_code == 200
    assert response.get_json()["available"] is True
    assert calls == [("MNQ", "INTRADAY_TREND", 500)]

    invalid_instrument = client.get(
        "/authoritative-verdict-history?instrument=ES&mode=SCALP"
    )
    assert invalid_instrument.status_code == 400
    assert invalid_instrument.get_json()["error"] == "invalid_instrument"

    invalid_mode = client.get(
        "/authoritative-verdict-history?instrument=MGC&mode=SWING"
    )
    assert invalid_mode.status_code == 400
    assert invalid_mode.get_json()["error"] == "invalid_mode"

    invalid_limit = client.get(
        "/authoritative-verdict-history?instrument=MGC&mode=SCALP&limit=many"
    )
    assert invalid_limit.status_code == 400
    assert invalid_limit.get_json()["error"] == "invalid_limit"