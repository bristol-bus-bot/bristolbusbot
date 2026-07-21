#!/usr/bin/env python3
"""Create or verify provenance for a finished timetable candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))

from timetable_control import validate  # noqa: E402


MANIFEST_VERSION = 1
GTFS_REQUIRED = (
    "agency.txt",
    "routes.txt",
    "stops.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar.txt",
    "shapes.txt",
)
GTFS_OPTIONAL = ("calendar_dates.txt",)
TABLES = (
    "agency",
    "routes",
    "stops",
    "trips",
    "stop_times",
    "calendar",
    "calendar_dates",
    "route_shapes",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, *, relative_to: Path | None = None) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"source is not a regular file: {path}")
    name = str(path.relative_to(relative_to)) if relative_to else path.name
    stat = path.stat()
    return {
        "name": name.replace("\\", "/"),
        "bytes": stat.st_size,
        "sha256": sha256_file(path),
        "modified_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def source_records(gtfs: Path, first_txc: Path, tnds: Path) -> dict[str, object]:
    records: dict[str, object] = {}
    gtfs_files = []
    for name in GTFS_REQUIRED:
        path = gtfs / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"required GTFS source is missing/empty: {path}")
        gtfs_files.append(file_record(path, relative_to=gtfs))
    for name in GTFS_OPTIONAL:
        path = gtfs / name
        if path.is_file():
            gtfs_files.append(file_record(path, relative_to=gtfs))
    records["bods_gtfs"] = {"files": gtfs_files}

    for key, directory in (("first_txc", first_txc), ("tnds", tnds)):
        archives = sorted(directory.glob("*.zip")) if directory.is_dir() else []
        if not archives:
            raise RuntimeError(f"required {key} archives are missing: {directory}")
        records[key] = {
            "files": [file_record(path, relative_to=directory) for path in archives],
        }
    return records


def key_summary(connection: sqlite3.Connection, sql: str) -> dict[str, object]:
    keys = sorted(tuple(row) for row in connection.execute(sql))
    encoded = json.dumps(keys, ensure_ascii=True, separators=(",", ":")).encode()
    return {
        "count": len(keys),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def database_summary(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        timetable_keys = key_summary(connection, """
            SELECT DISTINCT r.route_short_name, a.agency_noc,
                            COALESCE(t.direction_id, 0)
            FROM trips t
            JOIN routes r ON t.route_id=r.route_id
            JOIN agency a ON r.agency_id=a.agency_id
            WHERE t.shape_id IS NOT NULL AND t.shape_id != ''
        """)
        shape_keys = key_summary(connection, """
            SELECT DISTINCT route_name, operator_noc, direction_id
            FROM route_shapes
        """)
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()
    return {
        "tables": counts,
        "timetable_shape_keys": timetable_keys,
        "route_shape_keys": shape_keys,
        "journal_mode": str(mode).lower(),
    }


def create_manifest(*, database: Path, output: Path, gtfs: Path,
                    first_txc: Path, tnds: Path, builder_commit: str,
                    workflow_run_id: str, minimum_service_days: int) -> dict:
    if database.is_symlink() or not database.is_file():
        raise RuntimeError(f"candidate is not a regular file: {database}")
    validation = validate(
        database, minimum_service_days=minimum_service_days)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "builder": {
            "commit": builder_commit,
            "workflow_run_id": workflow_run_id,
            "repository": os.getenv("GITHUB_REPOSITORY", "local"),
            "ref": os.getenv("GITHUB_REF", "local"),
        },
        "artifact": {
            "filename": database.name,
            "bytes": database.stat().st_size,
            "sha256": sha256_file(database),
        },
        "database": database_summary(database),
        "validation": {
            "validator": "bbb-timetable-control-v2",
            "minimum_service_days": minimum_service_days,
            "result": validation,
        },
        "sources": source_records(gtfs, first_txc, tnds),
        "licence": {
            "identifier": "OGL-3.0",
            "attribution_file": "TIMETABLE_ARTIFACT_ATTRIBUTION.txt",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.new-{os.getpid()}")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return manifest


def verify_manifest(*, database: Path, manifest_path: Path,
                    minimum_service_days: int = 0) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise RuntimeError(
            f"unsupported manifest version: {manifest.get('manifest_version')!r}")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        raise RuntimeError("manifest has no artifact record")
    if artifact.get("filename") != database.name:
        raise RuntimeError("manifest filename does not match candidate")
    if artifact.get("bytes") != database.stat().st_size:
        raise RuntimeError("manifest byte size does not match candidate")
    if artifact.get("sha256") != sha256_file(database):
        raise RuntimeError("manifest SHA-256 does not match candidate")
    validation = validate(
        database, minimum_service_days=minimum_service_days)
    if manifest.get("database") != database_summary(database):
        raise RuntimeError("manifest database summary does not match candidate")
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--database", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--gtfs", required=True, type=Path)
    create.add_argument("--first-txc", required=True, type=Path)
    create.add_argument("--tnds", required=True, type=Path)
    create.add_argument("--builder-commit", required=True)
    create.add_argument("--workflow-run-id", required=True)
    create.add_argument("--minimum-service-days", type=int, default=14)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--database", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--minimum-service-days", type=int, default=0)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_manifest(
                database=args.database,
                output=args.output,
                gtfs=args.gtfs,
                first_txc=args.first_txc,
                tnds=args.tnds,
                builder_commit=args.builder_commit,
                workflow_run_id=args.workflow_run_id,
                minimum_service_days=args.minimum_service_days,
            )
            print(
                "manifest created: "
                f"sha256={result['artifact']['sha256']}, "
                f"latest_service={result['validation']['result']['latest_service']}")
        else:
            result = verify_manifest(
                database=args.database,
                manifest_path=args.manifest,
                minimum_service_days=args.minimum_service_days,
            )
            print(
                "manifest verified: "
                f"latest_service={result['latest_service']}, "
                f"route_shapes={result['route_shapes']}")
    except (OSError, ValueError, sqlite3.Error, RuntimeError,
            json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
