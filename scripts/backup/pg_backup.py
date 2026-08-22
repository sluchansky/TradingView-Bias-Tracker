#!/usr/bin/env python3
"""Create a portable PostgreSQL logical backup and evidence manifest.

The target database URL must be supplied at runtime through the named
environment variable. It is not written to output files or displayed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from backup_lib import (
    BackupToolError,
    build_manifest,
    connection_environment,
    ensure_external_destination,
    get_catalog,
    get_critical_evidence,
    get_server_version,
    require_independent_backup_host,
    run_process,
    utc_now,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a portable PostgreSQL backup outside the repository."
    )
    parser.add_argument("--environment", choices=("development", "production"), required=True)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Name of the environment variable holding the target URL; never its value.",
    )
    parser.add_argument("--output-dir", required=True, help="External backup destination.")
    parser.add_argument(
        "--confirm-external-destination",
        action="store_true",
        help="Required acknowledgement that the destination is outside Replit storage.",
    )
    parser.add_argument("--pg-dump", default="pg_dump")
    parser.add_argument("--psql", default="psql")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm_external_destination:
        raise BackupToolError(
            "Refusing to write a backup without --confirm-external-destination."
        )
    require_independent_backup_host()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise BackupToolError(
            f"Required environment variable {args.database_url_env} is not set; value not shown."
        )

    output_dir = ensure_external_destination(Path(args.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"bias-tracker-{args.environment}-{stamp}"
    dump_path = output_dir / f"{stem}.pgdump"
    manifest_path = output_dir / f"{stem}.manifest.json"
    if dump_path.exists() or manifest_path.exists():
        raise BackupToolError("Refusing to overwrite an existing backup or manifest.")

    child_env = connection_environment(database_url)
    run_process(
        [
            args.pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--file",
            str(dump_path),
        ],
        child_env,
    )
    schemas, tables, columns = get_catalog(database_url, args.psql)
    evidence = get_critical_evidence(database_url, args.psql, tables, columns)
    manifest = build_manifest(
        environment=args.environment,
        backup_timestamp_utc=utc_now(),
        postgresql_version=get_server_version(database_url, args.psql),
        schemas=schemas,
        tables=tables,
        critical_evidence=evidence,
        backup_path=dump_path,
    )
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "environment": args.environment,
                "backup": str(dump_path),
                "manifest": str(manifest_path),
                "sha256": manifest["backup"]["sha256"],
                "bytes": manifest["backup"]["bytes"],
                "schemas": manifest["schemas"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackupToolError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        raise SystemExit(2)