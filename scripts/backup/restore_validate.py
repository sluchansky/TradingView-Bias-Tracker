#!/usr/bin/env python3
"""Read-only validation of a restored PostgreSQL database against a manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from backup_lib import (
    BackupToolError,
    compare_manifest,
    get_catalog,
    get_critical_evidence,
    get_server_version,
    has_differences,
    load_manifest,
    sha256_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only comparison of a restored PostgreSQL database and backup manifest."
    )
    parser.add_argument("--environment", choices=("development", "production"), required=True)
    parser.add_argument("--backup", required=True, help="Original custom-format pg_dump file.")
    parser.add_argument("--manifest", required=True, help="Manifest generated beside the dump.")
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Name of the environment variable holding the restored DB URL; never its value.",
    )
    parser.add_argument("--psql", default="psql")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise BackupToolError(
            f"Required environment variable {args.database_url_env} is not set; value not shown."
        )
    backup_path = Path(args.backup).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    if not backup_path.is_file():
        raise BackupToolError("Backup file does not exist.")

    manifest = load_manifest(manifest_path)
    if manifest["environment"] != args.environment:
        raise BackupToolError(
            "Manifest environment does not match --environment; refusing an ambiguous validation."
        )
    expected_sha = manifest["backup"]["sha256"]
    actual_sha = sha256_file(backup_path)
    checksum_ok = actual_sha == expected_sha

    schemas, tables, columns = get_catalog(database_url, args.psql)
    evidence = get_critical_evidence(database_url, args.psql, tables, columns)
    differences = compare_manifest(manifest, schemas, tables, evidence)
    result = {
        "ok": checksum_ok and not has_differences(differences),
        "read_only": True,
        "environment": args.environment,
        "backup_checksum": {
            "expected": expected_sha,
            "actual": actual_sha,
            "matches": checksum_ok,
        },
        "restored_postgresql_version": get_server_version(database_url, args.psql),
        "differences": differences,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackupToolError as exc:
        print(f"restore validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)