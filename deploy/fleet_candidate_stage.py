#!/usr/bin/env python3
"""Stage the latest accepted fleet shadow candidate for guarded promotion.

Production invocation intentionally accepts no paths.  The fixed files keep a
timer or sudo caller from turning this helper into an arbitrary file copier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from enrichment_contracts import compare_fleet, validate_fleet


SHADOW_CANDIDATE = Path(
    "/var/lib/bristolbusbot/fleet-shadow/fbribuses.json")
SHADOW_REPORT = Path(
    "/var/lib/bristolbusbot/monitoring/fleet-shadow.json")
LIVE_FLEET = Path(
    "/var/lib/bristolbusbot/enrichment/fbribuses.json")
PROMOTION_CANDIDATE = Path(
    "/var/lib/bristolbusbot/enrichment/incoming/fbribuses.json")
MAXIMUM_FLEET_BYTES = 32 * 1024 * 1024
MAXIMUM_REPORT_BYTES = 4 * 1024 * 1024
MAXIMUM_REPORT_AGE = timedelta(hours=2)


class FleetStageError(RuntimeError):
    """The shadow result is not safe to stage."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _directory(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise FleetStageError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise FleetStageError(f"{label} is unsafe")
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
        raise FleetStageError(f"{label} is missing or unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise FleetStageError(f"shadow report {label} is invalid")
    return value


def _reported_digest(report: Mapping[str, object], field: str) -> str:
    section = _mapping(report.get(field), field)
    value = section.get("sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise FleetStageError(f"shadow report {field} digest is invalid")
    return value


def _finished_at(report: Mapping[str, object], now: datetime) -> datetime:
    value = report.get("finished_at")
    if not isinstance(value, str):
        raise FleetStageError("shadow report has no finish time")
    try:
        finished = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FleetStageError("shadow report finish time is invalid") from exc
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - finished.astimezone(timezone.utc)
    if age < timedelta(minutes=-5) or age > MAXIMUM_REPORT_AGE:
        raise FleetStageError("shadow report is stale")
    return finished


def _atomic_bytes(path: Path, raw: bytes) -> None:
    owner = _directory(path.parent, "promotion candidate directory")
    if path.is_symlink():
        raise FleetStageError("promotion candidate path is unsafe")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
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
    live_fleet: Path = LIVE_FLEET,
    promotion_candidate: Path = PROMOTION_CANDIDATE,
    now: datetime | None = None,
) -> dict[str, object]:
    """Recheck the shadow evidence and atomically fill the promotion inbox."""
    if len({str(path.resolve(strict=False)) for path in (
            shadow_candidate, shadow_report, live_fleet,
            promotion_candidate)}) != 4:
        raise FleetStageError("fleet staging paths must be distinct")
    candidate_raw = _regular_bytes(
        shadow_candidate, "shadow candidate", MAXIMUM_FLEET_BYTES)
    report_raw = _regular_bytes(
        shadow_report, "shadow report", MAXIMUM_REPORT_BYTES)
    live_raw = _regular_bytes(live_fleet, "live fleet", MAXIMUM_FLEET_BYTES)
    try:
        report = json.loads(report_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FleetStageError("shadow report is invalid JSON") from exc
    report = _mapping(report, "document")
    if report.get("schema") != 1 or report.get("mode") != "shadow-only" \
            or report.get("outcome") != "accepted-shadow" \
            or report.get("candidate_written") is not True \
            or report.get("promotion_attempted") is not False:
        raise FleetStageError("shadow report is not an accepted shadow run")
    finished = _finished_at(report, now or datetime.now(timezone.utc))
    candidate_digest = _digest(candidate_raw)
    live_digest = _digest(live_raw)
    if _reported_digest(report, "candidate") != candidate_digest:
        raise FleetStageError("shadow candidate no longer matches its report")
    if _reported_digest(report, "live") != live_digest:
        raise FleetStageError("live fleet changed since the shadow run")
    candidate_summary = validate_fleet(candidate_raw)
    live_summary = validate_fleet(live_raw)
    comparison = compare_fleet(candidate_summary, live_summary)
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
        "operator_transitions": comparison.get("operator_transitions", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        result = stage_candidate()
    except (FleetStageError, OSError, ValueError) as exc:
        parser.exit(1, f"fleet candidate not staged: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
