"""Unit tests for credential-safe backup and read-only validation helpers."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import backup_lib as lib  # noqa: E402


class BackupToolsTests(unittest.TestCase):
    def test_external_destination_rejects_repository_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            with self.assertRaises(lib.BackupToolError):
                lib.ensure_external_destination(root / "backups", root=root)

    def test_external_destination_accepts_path_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            destination = Path(temp) / "external"
            root.mkdir()
            self.assertEqual(lib.ensure_external_destination(destination, root=root), destination.resolve())

    def test_replit_runtime_is_rejected_as_a_final_backup_host(self) -> None:
        with self.assertRaises(lib.BackupToolError):
            lib.require_independent_backup_host({"REPL_ID": "workspace-id"})
        lib.require_independent_backup_host({})

    def test_redaction_removes_url_and_password(self) -> None:
        source = "failed postgresql://user:secret@host/db password=another-secret"
        output = lib.redact(source)
        self.assertNotIn("secret", output)
        self.assertIn("<redacted-postgres-url>", output)
        self.assertIn("password=<redacted>", output)

    def test_psql_keeps_connection_url_out_of_command(self) -> None:
        captured: dict[str, object] = {}

        def runner(command: list[str], env: dict[str, str]) -> str:
            captured["command"] = command
            captured["environment"] = env
            return "PostgreSQL 16\n"

        url = "postgresql://operator:very-secret@db.example/test"
        self.assertEqual(lib.get_server_version(url, "psql", runner), "PostgreSQL 16")
        self.assertNotIn(url, " ".join(captured["command"]))  # type: ignore[arg-type]
        child_env = captured["environment"]
        self.assertEqual(child_env["PGHOST"], "db.example")  # type: ignore[index]
        self.assertEqual(child_env["PGUSER"], "operator")  # type: ignore[index]
        self.assertEqual(child_env["PGDATABASE"], "test")  # type: ignore[index]
        self.assertEqual(child_env["PGPASSWORD"], "very-secret")  # type: ignore[index]

    def test_connection_environment_decodes_windows_safe_uri_components(self) -> None:
        environment = lib.connection_environment(
            "postgresql://operator:p%40ss%3Dword@db.example:5433/trading"
            "?sslmode=require&target_session_attrs=read-write"
        )
        self.assertEqual(environment["PGPASSWORD"], "p@ss=word")
        self.assertEqual(environment["PGPORT"], "5433")
        self.assertEqual(environment["PGSSLMODE"], "require")
        self.assertEqual(environment["PGTARGETSESSIONATTRS"], "read-write")

    def test_connection_environment_rejects_non_postgres_uri(self) -> None:
        with self.assertRaises(lib.BackupToolError):
            lib.connection_environment("https://not-a-database.example")

    def test_connection_environment_clears_stale_libpq_connection_settings(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PGHOST": "wrong-host",
                "PGDATABASE": "wrong-db",
                "PGSERVICE": "wrong-service",
                "PGSSLMODE": "disable",
            },
            clear=False,
        ):
            environment = lib.connection_environment(
                "postgresql://operator:secret@right-host/right-db?sslmode=require"
            )
        self.assertEqual(environment["PGHOST"], "right-host")
        self.assertEqual(environment["PGDATABASE"], "right-db")
        self.assertEqual(environment["PGSSLMODE"], "require")
        self.assertNotIn("PGSERVICE", environment)

    def test_connection_environment_requires_database_name(self) -> None:
        with self.assertRaises(lib.BackupToolError):
            lib.connection_environment("postgresql://operator:secret@db.example/")

    def test_manifest_contains_dynamic_coordinator_and_critical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dump = Path(temp) / "snapshot.pgdump"
            dump.write_bytes(b"portable dump")
            manifest = lib.build_manifest(
                environment="production",
                backup_timestamp_utc="2026-08-22T00:00:00+00:00",
                postgresql_version="PostgreSQL 16",
                schemas=["analysis_bot", "public"],
                tables=[
                    ("public", "ghost_coordinator_observations"),
                    ("public", "thesis_trade_evaluations"),
                ],
                critical_evidence={
                    "public.thesis_trade_evaluations": {
                        "present": True,
                        "row_count": 12,
                        "newest_timestamp": "2026-08-21 12:00:00+00",
                        "timestamp_column": "resolved_at",
                    },
                    "public.ghost_coordinator_observations": {
                        "present": False,
                        "row_count": None,
                        "newest_timestamp": None,
                        "timestamp_column": None,
                    },
                },
                backup_path=dump,
            )
            self.assertIn(
                {"schema": "public", "table": "ghost_coordinator_observations"},
                manifest["tables"],
            )
            self.assertEqual(
                manifest["backup"]["sha256"],
                hashlib.sha256(b"portable dump").hexdigest(),
            )

    def test_catalog_critical_rows_detect_missing_tables_without_assuming_parity(self) -> None:
        tables = [("public", "thesis_trade_evaluations")]
        columns = {("public", "thesis_trade_evaluations"): {"created_at", "resolved_at"}}

        def runner(_command: list[str], _env: dict[str, str]) -> str:
            return "9\t2026-08-22 12:00:00+00\n"

        evidence = lib.get_critical_evidence(
            "postgresql://unused/test",
            "psql",
            tables,
            columns,
            runner,
        )
        self.assertEqual(evidence["public.thesis_trade_evaluations"]["row_count"], 9)
        self.assertFalse(evidence["public.ghost_opportunities"]["present"])

    def test_critical_set_covers_current_portability_evidence_families(self) -> None:
        tables = set(lib.CRITICAL_TABLES)
        self.assertTrue(
            {
                ("public", "authoritative_verdict_history"),
                ("public", "canonical_ghost_evidence_records"),
                ("public", "canonical_ghost_unmatched_evidence_records"),
                ("public", "ghost_coordinator_observations"),
                ("public", "scalp_strategy_sim_trades"),
                ("public", "strategy_trades"),
                ("public", "native_journal"),
                ("public", "edge_ledger"),
                ("public", "internal_trade_snapshots"),
                ("public", "safety_overrides"),
                ("public", "execution_arm_audit"),
                ("analysis_bot", "strategy_trades"),
                ("analysis_bot", "backtest_runs"),
            }.issubset(tables)
        )

    def test_compare_manifest_reports_count_and_timestamp_mismatch(self) -> None:
        manifest = {
            "schemas": ["public"],
            "tables": [{"schema": "public", "table": "thesis_trade_evaluations"}],
            "critical_evidence": {
                "public.thesis_trade_evaluations": {
                    "present": True,
                    "row_count": 10,
                    "newest_timestamp": "2026-08-22T10:00:00+00:00",
                }
            },
        }
        differences = lib.compare_manifest(
            manifest,
            ["public"],
            [("public", "thesis_trade_evaluations")],
            {
                "public.thesis_trade_evaluations": {
                    "present": True,
                    "row_count": 9,
                    "newest_timestamp": "2026-08-22T09:00:00+00:00",
                }
            },
        )
        self.assertTrue(lib.has_differences(differences))
        self.assertEqual(len(differences["critical_evidence"]), 2)

    def test_manifest_loader_requires_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "bad.json"
            manifest.write_text(json.dumps({"environment": "development"}), encoding="utf-8")
            with self.assertRaises(lib.BackupToolError):
                lib.load_manifest(manifest)

    def test_checksum_changes_when_backup_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dump = Path(temp) / "snapshot.pgdump"
            dump.write_bytes(b"first")
            first_hash = lib.sha256_file(dump)
            dump.write_bytes(b"second")
            self.assertNotEqual(first_hash, lib.sha256_file(dump))


if __name__ == "__main__":
    unittest.main()