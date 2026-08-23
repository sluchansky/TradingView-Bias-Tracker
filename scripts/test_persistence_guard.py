"""Focused tests for the read-only PostgreSQL persistence guard."""

from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from persistence_guard import (  # noqa: E402
    PersistenceGuardError,
    automatic_migration_violations,
    assert_sql_policy,
    check_database_connection,
    sql_policy_violations,
)


def test_safe_idempotent_schema_sql_passes():
    assert_sql_policy(
        """
        CREATE TABLE IF NOT EXISTS example (id SERIAL PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS example_id_idx ON example (id);
        """,
        "fixture.sql",
    )


def test_destructive_sql_is_rejected():
    violations = sql_policy_violations("DROP TABLE evidence; TRUNCATE evidence;")
    assert "DROP" in violations
    assert "TRUNCATE" in violations


def test_data_writing_and_non_idempotent_ddl_are_rejected():
    violations = sql_policy_violations("UPDATE evidence SET value = 1;")
    assert "data-writing SQL" in violations
    violations = sql_policy_violations("CREATE TABLE evidence (id int);")
    assert "CREATE TABLE without IF NOT EXISTS" in violations


def test_unapproved_schema_forms_are_rejected():
    for sql in (
        "DROP VIEW evidence_view;",
        "DROP TYPE evidence_type;",
        "DROP SEQUENCE evidence_seq;",
        "CREATE FUNCTION refresh() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;",
        "CREATE MATERIALIZED VIEW evidence_mv AS SELECT 1;",
        "DO $$ BEGIN RAISE NOTICE 'unsafe'; END $$;",
    ):
        assert sql_policy_violations(sql), sql


def test_automatic_migrations_allow_only_reviewed_idempotent_schema_forms():
    assert automatic_migration_violations(
        "CREATE TABLE IF NOT EXISTS evidence (id int);"
        "CREATE UNIQUE INDEX IF NOT EXISTS evidence_id_idx ON evidence (id);"
    ) == []
    for sql in (
        'UPDATE "evidence" SET value = 1;',
        'COPY "evidence" FROM \'/tmp/evidence.csv\';',
        "DROP EXTENSION hstore;",
        "DROP FOREIGN TABLE remote_evidence;",
        "DROP OWNED BY app_user;",
        "SELECT 1;",
    ):
        assert automatic_migration_violations(sql), sql


def test_runtime_module_scan_allows_normal_persistence_but_not_unsafe_ddl():
    assert (
        sql_policy_violations(
            "INSERT INTO evidence (id) VALUES (1); CREATE TABLE IF NOT EXISTS safe (id int);",
            reject_data_writes=False,
        )
        == []
    )
    assert "DROP" in sql_policy_violations(
        "DROP TABLE evidence;",
        reject_data_writes=False,
    )


def test_sql_comments_do_not_trigger_policy():
    assert sql_policy_violations("-- DROP TABLE evidence\nSELECT 1;") == []


def test_boot_learning_warmup_is_configured_not_to_rewrite_evidence():
    app_source = (
        Path(__file__).resolve().parents[1]
        / "artifacts/tradingview-webhook/app.py"
    ).read_text(encoding="utf-8")
    assert "persist_derived_state=False" in app_source
    assert "normalize_historical_symbols=False" in app_source
    assert "_seed_scalp_library()                      # idempotent catalog seed" not in app_source


def test_read_only_database_probe_returns_catalog_facts_without_writes(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.calls = []
            self._results = [
                ("bias_tracker", "public", "16.4"),
                [("analysis_bot", 3), ("public", 12)],
            ]

        def execute(self, sql):
            self.calls.append(sql)

        def fetchone(self):
            return self._results.pop(0)

        def fetchall(self):
            return self._results.pop(0)

    cursor = FakeCursor()
    conn = Mock()
    conn.cursor.return_value = cursor
    monkeypatch.setenv("DATABASE_URL", "postgresql://redacted.example.invalid/db")

    result = check_database_connection(connect_fn=lambda *args, **kwargs: conn)

    assert result["database"] == "bias_tracker"
    assert result["public_tables"] == 12
    assert result["analysis_bot_tables"] == 3
    conn.set_session.assert_called_once_with(readonly=True, autocommit=True)
    assert len(cursor.calls) == 2
    assert not any(
        any(
            word in call.upper()
            for word in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE")
        )
        for call in cursor.calls
    )
    conn.close.assert_called_once()


def test_missing_database_url_is_fail_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        check_database_connection()
    except PersistenceGuardError as exc:
        assert "DATABASE_URL is not set" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL must block startup")