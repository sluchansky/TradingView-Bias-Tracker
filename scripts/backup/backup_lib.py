"""Shared, credential-safe PostgreSQL backup and restore-validation helpers.

The command-line tools in this directory deliberately use the connection URL only
through a child process environment variable.  It is never added to command-line
arguments, written to a manifest, or included in raised error messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


MANIFEST_VERSION = 1
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
URL_RE = re.compile(r"(?:postgres(?:ql)?://)[^\s'\"<>]+", re.IGNORECASE)
PASSWORD_RE = re.compile(r"(?i)(password=)([^\s]+)")
SYSTEM_SCHEMAS = {"pg_catalog", "information_schema"}

# The audit's evidence set. Missing tables are deliberately reported rather than
# treated as a failure to collect a manifest, because development and production
# do not necessarily have the same schema at a point in time.
CRITICAL_TABLES: tuple[tuple[str, str], ...] = (
    ("public", "thesis_trade_evaluations"),
    ("public", "decision_transitions"),
    ("public", "ghost_opportunities"),
    ("public", "ghost_experiments"),
    ("public", "ghost_experiment_results"),
    ("public", "scalp_strategy_sim_trades"),
    ("public", "visual_brain_observations"),
    ("public", "gate_audit_log"),
    ("public", "dual_sim_trades"),
    ("public", "strategy_trades"),
    ("analysis_bot", "strategy_trades"),
    ("public", "backtest_candles"),
    ("public", "backtest_datasets"),
    ("public", "backtest_runs"),
    ("analysis_bot", "backtest_candles"),
    ("analysis_bot", "backtest_datasets"),
    ("analysis_bot", "backtest_runs"),
    ("public", "journal_entries"),
    ("public", "journal_reviews"),
    ("public", "journal_attachments"),
    ("public", "native_journal"),
    ("public", "market_state_cache"),
    ("public", "safety_overrides"),
    ("public", "execution_arm_audit"),
    ("public", "open_trades"),
    ("public", "swing_theses"),
    ("public", "bot_training_state"),
)

TIMESTAMP_COLUMNS = (
    "resolved_at",
    "completed_at",
    "closed_at",
    "updated_at",
    "recorded_at",
    "timestamp",
    "created_at",
    "opened_at",
    "uploaded_at",
)

Runner = Callable[[list[str], dict[str, str]], str]


class BackupToolError(RuntimeError):
    """A safe error intended for operator-visible output."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise BackupToolError("Database catalog returned an invalid identifier.")
    return f'"{value}"'


def redact(text: str) -> str:
    """Remove common connection-string forms before surfacing command failures."""

    cleaned = URL_RE.sub("<redacted-postgres-url>", text)
    return PASSWORD_RE.sub(r"\1<redacted>", cleaned)


def run_process(command: list[str], env: dict[str, str]) -> str:
    """Run a database client without writing its effective environment anywhere."""

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = redact((exc.stderr or exc.stdout or "database client failed").strip())
        raise BackupToolError(f"Database client failed: {detail}") from None
    except OSError as exc:
        raise BackupToolError(f"Could not start database client: {exc}") from None
    return completed.stdout


def connection_environment(url: str) -> dict[str, str]:
    """Build child-only libpq environment without exposing the URL as an argument."""

    environment = os.environ.copy()
    environment["PGDATABASE"] = url
    return environment


def run_psql(url: str, sql: str, psql_path: str, runner: Runner = run_process) -> str:
    command = [
        psql_path,
        "-X",
        "-q",
        "-t",
        "-A",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    return runner(command, connection_environment(url))


def parse_rows(output: str) -> list[list[str]]:
    return [line.split("\t") for line in output.splitlines() if line.strip()]


def get_server_version(url: str, psql_path: str, runner: Runner = run_process) -> str:
    return run_psql(url, "SELECT version();", psql_path, runner).strip()


def get_catalog(
    url: str,
    psql_path: str,
    runner: Runner = run_process,
) -> tuple[list[str], list[tuple[str, str]], dict[tuple[str, str], set[str]]]:
    schemas_sql = """
        SELECT nspname
        FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog', 'information_schema')
          AND nspname NOT LIKE 'pg_toast%%'
          AND nspname NOT LIKE 'pg_temp_%%'
        ORDER BY nspname;
    """
    tables_sql = """
        SELECT n.nspname, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%'
          AND n.nspname NOT LIKE 'pg_temp_%%'
        ORDER BY n.nspname, c.relname;
    """
    columns_sql = """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, ordinal_position;
    """
    schemas = [row[0] for row in parse_rows(run_psql(url, schemas_sql, psql_path, runner))]
    tables = [tuple(row[:2]) for row in parse_rows(run_psql(url, tables_sql, psql_path, runner))]
    columns: dict[tuple[str, str], set[str]] = {}
    for row in parse_rows(run_psql(url, columns_sql, psql_path, runner)):
        if len(row) >= 3:
            columns.setdefault((row[0], row[1]), set()).add(row[2])
    return schemas, tables, columns


def newest_column(columns: Iterable[str]) -> str | None:
    known = set(columns)
    return next((column for column in TIMESTAMP_COLUMNS if column in known), None)


def get_critical_evidence(
    url: str,
    psql_path: str,
    tables: Iterable[tuple[str, str]],
    columns: dict[tuple[str, str], set[str]],
    runner: Runner = run_process,
) -> dict[str, dict[str, Any]]:
    existing = set(tables)
    result: dict[str, dict[str, Any]] = {}
    for schema, table in CRITICAL_TABLES:
        key = f"{schema}.{table}"
        if (schema, table) not in existing:
            result[key] = {
                "present": False,
                "row_count": None,
                "newest_timestamp": None,
                "timestamp_column": None,
            }
            continue

        timestamp_column = newest_column(columns.get((schema, table), set()))
        quoted_table = f"{safe_identifier(schema)}.{safe_identifier(table)}"
        if timestamp_column:
            sql = (
                f"SELECT COUNT(*)::text, MAX({safe_identifier(timestamp_column)})::text "
                f"FROM {quoted_table};"
            )
        else:
            sql = f"SELECT COUNT(*)::text FROM {quoted_table};"
        row = parse_rows(run_psql(url, sql, psql_path, runner))[0]
        result[key] = {
            "present": True,
            "row_count": int(row[0]),
            "newest_timestamp": row[1] if timestamp_column and len(row) > 1 and row[1] else None,
            "timestamp_column": timestamp_column,
        }
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_external_destination(path: Path, root: Path | None = None) -> Path:
    resolved = path.expanduser().resolve()
    repo = (root or repository_root()).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        return resolved
    raise BackupToolError(
        "Backup output must be outside the repository. Use encrypted external or "
        "independently managed storage, not the Replit workspace."
    )


def require_independent_backup_host(environment: dict[str, str] | None = None) -> None:
    """Reject export execution from the Replit runtime itself.

    A backup written from this workspace could still be lost with the workspace,
    even if its path is outside the Git repository.  The operator must run the
    export from Windows or another independently managed host.
    """

    values = environment if environment is not None else os.environ
    replit_markers = ("REPL_ID", "REPLIT_DEV_DOMAIN", "REPLIT_DOMAINS")
    if any(values.get(marker) for marker in replit_markers):
        raise BackupToolError(
            "Refusing to write a final backup from the Replit runtime. "
            "Run this tool from Windows or another independent host."
        )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupToolError(f"Could not read manifest: {exc}") from None
    required = {
        "manifest_version",
        "environment",
        "backup_timestamp_utc",
        "postgresql_version",
        "schemas",
        "tables",
        "critical_evidence",
        "backup",
    }
    if not required.issubset(data):
        missing = ", ".join(sorted(required - set(data)))
        raise BackupToolError(f"Manifest is missing required fields: {missing}")
    return data


def build_manifest(
    *,
    environment: str,
    backup_timestamp_utc: str,
    postgresql_version: str,
    schemas: list[str],
    tables: list[tuple[str, str]],
    critical_evidence: dict[str, dict[str, Any]],
    backup_path: Path,
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "environment": environment,
        "backup_timestamp_utc": backup_timestamp_utc,
        "postgresql_version": postgresql_version,
        "schemas": schemas,
        "tables": [{"schema": schema, "table": table} for schema, table in tables],
        "critical_evidence": critical_evidence,
        "backup": {
            "filename": backup_path.name,
            "format": "pg_dump custom",
            "bytes": backup_path.stat().st_size,
            "sha256": sha256_file(backup_path),
        },
    }


def compare_manifest(
    manifest: dict[str, Any],
    schemas: list[str],
    tables: list[tuple[str, str]],
    critical_evidence: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    expected_schemas = set(manifest["schemas"])
    actual_schemas = set(schemas)
    expected_tables = {
        (entry["schema"], entry["table"]) for entry in manifest["tables"]
    }
    actual_tables = set(tables)
    differences: dict[str, list[str]] = {
        "missing_schemas": sorted(expected_schemas - actual_schemas),
        "missing_tables": [
            f"{schema}.{table}" for schema, table in sorted(expected_tables - actual_tables)
        ],
        "critical_evidence": [],
    }
    for table_name, expected in manifest["critical_evidence"].items():
        actual = critical_evidence.get(table_name)
        if actual is None:
            differences["critical_evidence"].append(f"{table_name}: not inspected")
            continue
        for field in ("present", "row_count", "newest_timestamp"):
            if expected.get(field) != actual.get(field):
                differences["critical_evidence"].append(
                    f"{table_name}: {field} expected {expected.get(field)!r}, "
                    f"got {actual.get(field)!r}"
                )
    return differences


def has_differences(differences: dict[str, list[str]]) -> bool:
    return any(values for values in differences.values())