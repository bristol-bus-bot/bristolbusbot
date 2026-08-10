#!/usr/bin/env python3
"""Stage one fresh, exact-coverage locality shadow for guarded promotion."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from enrichment_contracts import compare_localities, validate_localities


SHADOW_CANDIDATE = Path(
    "/var/lib/bristolbusbot/locality-shadow/stop_localities.json")
SHADOW_REPORT = Path(
    "/var/lib/bristolbusbot/monitoring/locality-shadow.json")
LIVE_LOCALITIES = Path(
    "/var/lib/bristolbusbot/enrichment/stop_localities.json")
LIVE_TIMETABLE = Path(
    "/var/lib/bristolbusbot/pipeline/timetable.db")
PROMOTION_CANDIDATE = Path(
    "/var/lib/bristolbusbot/enrichment/incoming/stop_localities.json")
BOUNDARY_EDITION = "December 2025"
BOUNDARY_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "WD_DEC_2025_UK_BSC/FeatureServer/0/query"
)
AUTHORITY_CODES = {
    "E06000022", "E06000023", "E06000024", "E06000025",
}
MAXIMUM_LOCALITY_BYTES = 16 * 1024 * 1024
MAXIMUM_REPORT_BYTES = 4 * 1024 * 1024
MAXIMUM_REPORT_AGE = timedelta(hours=2)
WECA_BBOX = (51.2731, 51.6773, -3.1151, -2.2521)


class LocalityStageError(RuntimeError):
    """The locality shadow evidence is not safe to stage."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _directory(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise LocalityStageError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise LocalityStageError(f"{label} is unsafe")
    return details


def _regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    descriptor = None
    try:
        if path.is_symlink():
            raise OSError("symbolic links are not accepted")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size <= 0 \
                or details.st_size > maximum:
            raise OSError("not a bounded regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(maximum + 1)
        if len(raw) != details.st_size or len(raw) > maximum:
            raise OSError("file changed while it was being read")
        return raw
    except OSError as exc:
        raise LocalityStageError(f"{label} is missing or unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise LocalityStageError(f"shadow report {label} is invalid")
    return value


def _reported_digest(report: Mapping[str, object], field: str) -> str:
    section = _mapping(report.get(field), field)
    value = section.get("sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise LocalityStageError(f"shadow report {field} digest is invalid")
    return value


def _finished_at(report: Mapping[str, object], now: datetime) -> datetime:
    value = report.get("finished_at")
    if not isinstance(value, str):
        raise LocalityStageError("shadow report has no finish time")
    try:
        finished = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocalityStageError("shadow report finish time is invalid") from exc
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - finished.astimezone(timezone.utc)
    if age < timedelta(minutes=-5) or age > MAXIMUM_REPORT_AGE:
        raise LocalityStageError("shadow report is stale")
    return finished


def _timetable_codes(path: Path) -> set[str]:
    if path.is_symlink() or not path.is_file():
        raise LocalityStageError("live timetable is unavailable")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT DISTINCT TRIM(stop_code) FROM stops "
                "WHERE stop_code IS NOT NULL AND TRIM(stop_code) <> '' "
                "AND stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?",
                WECA_BBOX,
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LocalityStageError(f"live timetable query failed: {exc}") from exc
    codes = {str(row[0]).strip() for row in rows if str(row[0]).strip()}
    if not codes:
        raise LocalityStageError("live timetable has no scoped stops")
    return codes


def _check_boundary(report: Mapping[str, object], candidate: str,
                    live: str) -> None:
    boundary = _mapping(report.get("boundary"), "boundary")
    mode = boundary.get("mode")
    if mode == "reused-live":
        if candidate != live:
            raise LocalityStageError("reused-live candidate differs from live data")
        return
    if mode != "fetched" or boundary.get("edition") != BOUNDARY_EDITION \
            or boundary.get("source") != BOUNDARY_URL \
            or boundary.get("feature_count") != 130 \
            or set(boundary.get("authority_codes") or []) != AUTHORITY_CODES:
        raise LocalityStageError("shadow report boundary provenance is not approved")
    boundary_digest = boundary.get("sha256")
    if not isinstance(boundary_digest, str) or len(boundary_digest) != 64:
        raise LocalityStageError("shadow report boundary digest is invalid")


def _atomic_bytes(path: Path, raw: bytes) -> None:
    owner = _directory(path.parent, "promotion candidate directory")
    if path.is_symlink():
        raise LocalityStageError("promotion candidate path is unsafe")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if hasattr(os, "chown"):
            os.chown(temporary_path, owner.st_uid, owner.st_gid)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def stage_candidate(
    *,
    shadow_candidate: Path = SHADOW_CANDIDATE,
    shadow_report: Path = SHADOW_REPORT,
    live_localities: Path = LIVE_LOCALITIES,
    live_timetable: Path = LIVE_TIMETABLE,
    promotion_candidate: Path = PROMOTION_CANDIDATE,
    now: datetime | None = None,
) -> dict[str, object]:
    paths = (shadow_candidate, shadow_report, live_localities,
             live_timetable, promotion_candidate)
    if len({str(path.resolve(strict=False)) for path in paths}) != len(paths):
        raise LocalityStageError("locality staging paths must be distinct")
    candidate_raw = _regular_bytes(
        shadow_candidate, "shadow candidate", MAXIMUM_LOCALITY_BYTES)
    report_raw = _regular_bytes(
        shadow_report, "shadow report", MAXIMUM_REPORT_BYTES)
    live_raw = _regular_bytes(
        live_localities, "live locality file", MAXIMUM_LOCALITY_BYTES)
    try:
        report = json.loads(report_raw)
        candidate_value = json.loads(candidate_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalityStageError("shadow evidence is invalid JSON") from exc
    report = _mapping(report, "document")
    if report.get("schema") != 1 or report.get("mode") != "shadow-only" \
            or report.get("outcome") != "accepted-shadow" \
            or report.get("candidate_written") is not True \
            or report.get("promotion_attempted") is not False:
        raise LocalityStageError("shadow report is not an accepted shadow run")
    finished = _finished_at(report, now or datetime.now(timezone.utc))
    candidate_digest = _digest(candidate_raw)
    live_digest = _digest(live_raw)
    if _reported_digest(report, "candidate") != candidate_digest:
        raise LocalityStageError("shadow candidate no longer matches its report")
    if _reported_digest(report, "live") != live_digest:
        raise LocalityStageError("live localities changed since the shadow run")
    _check_boundary(report, candidate_digest, live_digest)

    coverage = _mapping(report.get("coverage"), "coverage")
    if coverage.get("missing") != 0 or coverage.get("extra") != 0:
        raise LocalityStageError("shadow report does not prove exact coverage")
    if not isinstance(candidate_value, dict):
        raise LocalityStageError("shadow candidate is not an object")
    timetable_codes = _timetable_codes(live_timetable)
    candidate_codes = {str(code).strip() for code in candidate_value}
    if candidate_codes != timetable_codes:
        raise LocalityStageError("shadow candidate does not match the live timetable")

    candidate_summary = validate_localities(candidate_raw)
    live_summary = validate_localities(live_raw)
    comparison = compare_localities(candidate_summary, live_summary)
    if candidate_summary["records"] != len(timetable_codes):
        raise LocalityStageError("candidate record count does not match its keys")
    _atomic_bytes(promotion_candidate, candidate_raw)
    return {
        "status": "staged",
        "finished_at": finished.astimezone(timezone.utc).isoformat(),
        "candidate": {
            "sha256": candidate_digest,
            "records": candidate_summary["records"],
        },
        "live": {
            "sha256": live_digest,
            "records": live_summary["records"],
        },
        "coverage": dict(coverage),
        "comparison": comparison,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        result = stage_candidate()
    except (LocalityStageError, OSError, ValueError) as exc:
        parser.exit(1, f"locality candidate not staged: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
