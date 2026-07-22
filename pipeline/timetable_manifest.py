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

from timetable_control import VALIDATOR_ID, validate  # noqa: E402


MANIFEST_VERSION = 5
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
    "stop_routes",
    "route_service_editions",
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


def source_records(gtfs: Path, first_txc: Path, tnds: Path,
                   source_status: Path) -> dict[str, object]:
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

    first_archives = sorted(first_txc.glob("*.zip")) \
        if first_txc.is_dir() else []
    if not first_archives:
        raise RuntimeError(
            f"required first_txc archives are missing: {first_txc}")
    records["first_txc"] = {
        "status": "used",
        "files": [
            file_record(path, relative_to=first_txc)
            for path in first_archives
        ],
    }

    if source_status.is_symlink() or not source_status.is_file():
        raise RuntimeError(f"source-status record is missing: {source_status}")
    try:
        status_payload = json.loads(source_status.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid source-status record: {exc}") from exc
    if status_payload.get("schema") != 1:
        raise RuntimeError("unsupported source-status schema")
    tnds_status = status_payload.get("tnds")
    if not isinstance(tnds_status, dict):
        raise RuntimeError("source-status record has no TNDS decision")
    status = tnds_status.get("status")
    missing_before = tnds_status.get("missing_before_fallback")
    if status not in {"fallback_used", "not_needed"}:
        raise RuntimeError(f"invalid TNDS source status: {status!r}")
    if not isinstance(missing_before, list) or not all(
            isinstance(route, str) and route for route in missing_before):
        raise RuntimeError("invalid TNDS missing-route record")
    if status == "not_needed" and missing_before:
        raise RuntimeError(
            "TNDS cannot be not_needed when required routes were missing")
    if status == "fallback_used" and not missing_before:
        raise RuntimeError(
            "TNDS fallback_used status has no missing-route reason")

    tnds_archives = sorted(tnds.glob("*.zip")) if tnds.is_dir() else []
    if status == "fallback_used" and not tnds_archives:
        raise RuntimeError(f"required TNDS fallback archive is missing: {tnds}")
    records["tnds"] = {
        "status": status,
        "missing_before_fallback": missing_before,
        "files": [
            file_record(path, relative_to=tnds)
            for path in tnds_archives
        ] if status == "fallback_used" else [],
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
                    first_txc: Path, tnds: Path, source_status: Path,
                    builder_commit: str,
                    workflow_run_id: str, build_started_utc: str,
                    minimum_service_days: int) -> dict:
    if database.is_symlink() or not database.is_file():
        raise RuntimeError(f"candidate is not a regular file: {database}")
    validation = validate(
        database, minimum_service_days=minimum_service_days,
        require_stop_routes=True)
    try:
        started = datetime.fromisoformat(build_started_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("invalid build start timestamp") from exc
    if started.tzinfo is None:
        raise RuntimeError("build start timestamp must include a timezone")
    finished = datetime.now(timezone.utc)
    started = started.astimezone(timezone.utc)
    if started > finished:
        raise RuntimeError("build start timestamp is after build finish")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_utc": finished.isoformat(),
        "build": {
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
        },
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
            "validator": VALIDATOR_ID,
            "minimum_service_days": minimum_service_days,
            "result": validation,
        },
        "sources": source_records(gtfs, first_txc, tnds, source_status),
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
    validation_record = manifest.get("validation")
    if not isinstance(validation_record, dict):
        raise RuntimeError("manifest has no validation record")
    if validation_record.get("validator") != VALIDATOR_ID:
        raise RuntimeError("manifest names an unsupported validator")
    recorded_minimum = validation_record.get("minimum_service_days")
    if (isinstance(recorded_minimum, bool)
            or not isinstance(recorded_minimum, int)
            or recorded_minimum < minimum_service_days):
        raise RuntimeError("manifest validation window is too short")
    validation = validate(
        database, minimum_service_days=minimum_service_days,
        require_stop_routes=True)
    if validation_record.get("result") != validation:
        raise RuntimeError("manifest validation result does not match candidate")
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
    create.add_argument("--source-status", required=True, type=Path)
    create.add_argument("--builder-commit", required=True)
    create.add_argument("--workflow-run-id", required=True)
    create.add_argument("--build-started-utc", required=True)
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
                source_status=args.source_status,
                builder_commit=args.builder_commit,
                workflow_run_id=args.workflow_run_id,
                build_started_utc=args.build_started_utc,
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
