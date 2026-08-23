#!/usr/bin/env python3
"""Read-only PostgreSQL persistence and startup-safety guard.

The guard itself never creates tables, writes rows, runs migrations, or changes
database settings. It rejects destructive/non-idempotent boot schema SQL and
all data-writing automatic migrations. Unless --source-only is supplied, it
reconnects to PostgreSQL in read-only mode to verify that startup is pointed at
an existing non-template database. Normal event-driven application persistence
remains outside this guard so research observation can continue after boot.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable


class PersistenceGuardError(RuntimeError):
    """A persistence safety invariant was not satisfied."""


_SQL_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\r\n]*", re.DOTALL)

# These are schema/startup surfaces, not application CRUD handlers. Operator
# CRUD and event-driven research persistence are intentionally outside this
# boot/deploy policy check.
_POLICY_FILES = (
    "scripts/prod-start.sh",
    "artifacts/tradingview-webhook/orb_engine.py",
    "artifacts/tradingview-webhook/canonical_market_state.py",
)
_MIGRATIONS_DIR = "lib/db/migrations"

_DESTRUCTIVE_SQL = (
    (
        re.compile(
            r"\bDROP\s+(?:DATABASE|SCHEMA|TABLE|INDEX|VIEW|MATERIALIZED\s+VIEW|"
            r"SEQUENCE|TYPE|FUNCTION|PROCEDURE|TRIGGER|RULE|DOMAIN)\b",
            re.I,
        ),
        "DROP",
    ),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
    (re.compile(r"\bCREATE\s+DATABASE\b", re.I), "CREATE DATABASE"),
    (
        re.compile(
            r"\b(?:INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM|MERGE\s+INTO|COPY\s+\w+\s+FROM)\b",
            re.I,
        ),
        "data-writing SQL",
    ),
    (
        re.compile(
            r"\bALTER\s+(?:TABLE|SCHEMA|TYPE|DOMAIN|FUNCTION|PROCEDURE)\b",
            re.I,
        ),
        "ALTER requires an explicit idempotency review",
    ),
    (re.compile(r"\bDO\s+(?:\$|\bBEGIN\b)", re.I), "procedural DO block"),
)
_NON_IDEMPOTENT_DDL = (
    (
        re.compile(r"\bCREATE\s+TABLE\b(?!\s+IF\s+NOT\s+EXISTS\b)", re.I),
        "CREATE TABLE without IF NOT EXISTS",
    ),
    (
        re.compile(
            r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b(?!\s+IF\s+NOT\s+EXISTS\b)",
            re.I,
        ),
        "CREATE INDEX without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bCREATE\s+SCHEMA\b(?!\s+IF\s+NOT\s+EXISTS\b)", re.I),
        "CREATE SCHEMA without IF NOT EXISTS",
    ),
    (
        re.compile(
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+VIEW|SEQUENCE|TYPE|"
            r"VIEW|EXTENSION|FUNCTION|PROCEDURE|TRIGGER|RULE|DOMAIN|AGGREGATE|"
            r"COLLATION|CAST|OPERATOR)\b",
            re.I,
        ),
        "unapproved CREATE",
    ),
)
_AUTOMATIC_MIGRATION_ALLOWLIST = re.compile(
    r"^\s*CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\s+IF\s+NOT\s+EXISTS\b",
    re.I,
)


def _sql_without_comments(text: str) -> str:
    return _SQL_COMMENT_RE.sub(" ", text)


def sql_policy_violations(
    sql: str,
    *,
    reject_data_writes: bool = True,
) -> list[str]:
    """Return destructive or non-idempotent SQL policy violations.

    Runtime modules may contain normal persistence INSERT/UPDATE statements.
    Their startup schema DDL is still checked, but only automatic migrations
    are forbidden from carrying any data-writing SQL.
    """

    normalized = _sql_without_comments(sql)
    violations: list[str] = []
    for pattern, label in _DESTRUCTIVE_SQL:
        if label == "data-writing SQL" and not reject_data_writes:
            continue
        if pattern.search(normalized):
            violations.append(label)
    for pattern, label in _NON_IDEMPOTENT_DDL:
        if pattern.search(normalized):
            violations.append(label)
    return violations


def assert_sql_policy(
    sql: str,
    label: str,
    *,
    reject_data_writes: bool = True,
) -> None:
    violations = sql_policy_violations(sql, reject_data_writes=reject_data_writes)
    if violations:
        joined = ", ".join(dict.fromkeys(violations))
        raise PersistenceGuardError(
            f"{label} violates the non-destructive persistence policy: {joined}"
        )


def automatic_migration_violations(sql: str) -> list[str]:
    """Return violations for automatic migrations.

    Automatic migration execution is deliberately fail-closed: only the two
    reviewed idempotent schema forms are allowed. Splitting on semicolons is
    conservative—SQL with embedded procedural blocks is rejected rather than
    interpreted as safe.
    """

    normalized = _sql_without_comments(sql)
    violations = sql_policy_violations(normalized)
    for statement in normalized.split(";"):
        if statement.strip() and not _AUTOMATIC_MIGRATION_ALLOWLIST.match(statement):
            violations.append("unapproved automatic migration statement")
    return list(dict.fromkeys(violations))


def assert_automatic_migration_policy(sql: str, label: str) -> None:
    violations = automatic_migration_violations(sql)
    if violations:
        raise PersistenceGuardError(
            f"{label} violates the non-destructive persistence policy: "
            f"{', '.join(violations)}"
        )


def check_source_policy(source_root: Path) -> dict[str, int]:
    """Check startup/deployment policy files and every SQL migration."""

    checked = 0
    for relative in _POLICY_FILES:
        path = source_root / relative
        if not path.is_file():
            raise PersistenceGuardError(
                f"required persistence policy file is missing: {relative}"
            )
        assert_sql_policy(
            path.read_text(encoding="utf-8"),
            relative,
            reject_data_writes=False,
        )
        checked += 1

    migrations_dir = source_root / _MIGRATIONS_DIR
    if not migrations_dir.is_dir():
        raise PersistenceGuardError(
            f"required migrations directory is missing: {_MIGRATIONS_DIR}"
        )
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        raise PersistenceGuardError(
            "no SQL migrations found; refusing an unverifiable startup"
        )
    for path in migration_files:
        assert_automatic_migration_policy(
            path.read_text(encoding="utf-8"),
            str(path.relative_to(source_root)),
        )
        checked += 1

    return {
        "policy_files": len(_POLICY_FILES),
        "migration_files": len(migration_files),
        "checked": checked,
    }


def check_database_connection(
    database_url_env: str = "DATABASE_URL",
    connect_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Verify PostgreSQL using SELECT-only access and no schema mutation."""

    dsn = os.environ.get(database_url_env)
    if not dsn:
        raise PersistenceGuardError(
            f"{database_url_env} is not set; refusing to start without a known persistent database"
        )

    if connect_fn is None:
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise PersistenceGuardError(
                "psycopg2 is unavailable for the read-only database check"
            ) from exc
        connect_fn = psycopg2.connect

    conn = None
    try:
        conn = connect_fn(dsn, connect_timeout=5)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT current_database(), current_schema(), current_setting('server_version')"
        )
        database, schema, server_version = cur.fetchone()
        if database in {"template0", "template1"}:
            raise PersistenceGuardError(
                f"connected to PostgreSQL template database {database!r}; refusing startup"
            )
        cur.execute(
            """
            SELECT table_schema, COUNT(*)
            FROM information_schema.tables
            WHERE table_schema IN ('public', 'analysis_bot')
            GROUP BY table_schema
            ORDER BY table_schema
            """
        )
        table_counts = {row[0]: int(row[1]) for row in cur.fetchall()}
        if table_counts.get("public", 0) == 0:
            raise PersistenceGuardError(
                "connected database has no public tables; refusing to treat an empty database as existing state"
            )
        return {
            "database": database,
            "schema": schema,
            "server_version": server_version,
            "public_tables": table_counts.get("public", 0),
            "analysis_bot_tables": table_counts.get("analysis_bot", 0),
        }
    except PersistenceGuardError:
        raise
    except Exception as exc:
        # Some PostgreSQL drivers include the DSN in exception text.
        raise PersistenceGuardError(
            f"read-only PostgreSQL reconnect check failed ({type(exc).__name__})"
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="check source policy without opening PostgreSQL",
    )
    args = parser.parse_args(argv)

    try:
        source_summary = check_source_policy(args.source_root.resolve())
        print(
            "persistence guard: source policy OK "
            f"({source_summary['policy_files']} startup files, "
            f"{source_summary['migration_files']} migrations)"
        )
        if args.source_only:
            print("persistence guard: database check skipped (--source-only)")
        else:
            db_summary = check_database_connection(args.database_url_env)
            print(
                "persistence guard: read-only PostgreSQL reconnect OK "
                f"(database={db_summary['database']!s}, "
                f"public_tables={db_summary['public_tables']}, "
                f"analysis_bot_tables={db_summary['analysis_bot_tables']})"
            )
        return 0
    except PersistenceGuardError as exc:
        print(f"persistence guard: BLOCKED — {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            "persistence guard: BLOCKED — filesystem check failed "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())