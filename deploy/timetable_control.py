#!/usr/bin/env python3
"""Validate, atomically promote, or roll back the canonical timetable."""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path("/var/lib/bristolbusbot/pipeline")
EXPECTED_FBRI = {"1", "2", "42", "43", "44", "45", "75", "76", "X1", "m1"}


def paths(root: Path = ROOT) -> tuple[Path, Path, Path, Path]:
    return (
        root / "timetable.db",
        root / ".timetable.db.upload",
        root / "timetable.db.previous",
        root / ".timetable.db.failed",
    )


def validate(path: Path, *, today: date | None = None) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"timetable is not a regular file: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check returned {integrity!r}")
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "delete":
            raise RuntimeError("static timetable must use DELETE journal mode")
        routes = {row[0] for row in connection.execute(
            "SELECT DISTINCT r.route_short_name FROM routes r "
            "JOIN agency a ON r.agency_id=a.agency_id WHERE a.agency_noc='FBRI'")}
        latest = max((row[0] for row in connection.execute(
            "SELECT MAX(end_date) FROM calendar UNION ALL "
            "SELECT MAX(date) FROM calendar_dates WHERE exception_type=1") if row[0]),
            default=None)
        shape_count = connection.execute("SELECT COUNT(*) FROM route_shapes").fetchone()[0]
    finally:
        connection.close()
    missing = sorted(EXPECTED_FBRI - routes)
    today_text = (today or date.today()).strftime("%Y%m%d")
    if missing:
        raise RuntimeError(f"missing required First routes: {', '.join(missing)}")
    if latest is None or latest < today_text:
        raise RuntimeError(f"timetable service window is stale: {latest or 'missing'}")
    if shape_count <= 0:
        raise RuntimeError("timetable contains no route shapes")
    return {"latest_service": latest, "first_routes": len(routes),
            "route_shapes": shape_count}


def promote(root: Path = ROOT) -> dict[str, object]:
    live, upload, previous, _ = paths(root)
    result = validate(upload)
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    previous.unlink(missing_ok=True)
    if live.exists():
        os.link(live, previous)
    os.chmod(upload, 0o600)
    os.replace(upload, live)
    return result


def rollback(root: Path = ROOT) -> dict[str, object]:
    live, _, previous, failed = paths(root)
    if not previous.is_file() or previous.is_symlink():
        raise RuntimeError("no safe timetable rollback copy exists")
    failed.unlink(missing_ok=True)
    if live.exists():
        os.replace(live, failed)
    os.replace(previous, live)
    return validate(live)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "promote", "rollback"))
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    live, _, _, _ = paths()
    if args.action == "validate":
        result = validate(args.path or live)
    elif args.action == "promote":
        if args.path is not None:
            raise SystemExit("promote always uses the fixed upload path")
        result = promote()
    else:
        if args.path is not None:
            raise SystemExit("rollback always uses the fixed previous path")
        result = rollback()
    print("timetable valid: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
