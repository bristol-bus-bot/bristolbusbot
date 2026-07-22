#!/usr/bin/env python3
"""Validate, atomically promote, or roll back the canonical timetable."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_MODULES = PROJECT_ROOT / "pipeline"
INSTALLED_MODULES = Path("/usr/local/libexec/bristolbusbot-timetable")
for module_root in (PIPELINE_MODULES, INSTALLED_MODULES):
    if module_root.is_dir():
        sys.path.insert(0, str(module_root))

from timetable_editions import validate_database as validate_route_editions  # noqa: E402


ROOT = Path("/var/lib/bristolbusbot/pipeline")
EXPECTED_FBRI = {"1", "2", "42", "43", "44", "45", "75", "76", "X1", "m1"}
VALIDATOR_ID = "bbb-timetable-control-v4"
REQUIRED_COLUMNS = {
    "agency": {"agency_id", "agency_noc"},
    "routes": {"route_id", "agency_id", "route_short_name"},
    "stops": {"stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"},
    "trips": {"trip_id", "route_id", "service_id", "direction_id", "shape_id"},
    "stop_times": {"trip_id", "stop_id", "stop_sequence"},
    "calendar": {"service_id", "end_date"},
    "calendar_dates": {"service_id", "date", "exception_type"},
    "route_shapes": {
        "route_name", "operator_noc", "direction_id", "variant", "points_json",
    },
}
STOP_ROUTES_COLUMNS = {"stop_code", "route_short_name"}
REQUIRED_INDEXES = {
    "idx_trips_vjc",
    "idx_routes_agency",
    "idx_stop_times_stop",
    "idx_stop_times_trip_seq",
    "idx_trips_route_dir",
    "idx_trips_service",
    "idx_routes_short_name",
    "idx_calendar_dates_service",
    "idx_calendar_dates_date",
    "idx_stops_code",
    "idx_stops_latlon",
    "idx_agency_noc",
}
MAX_SHAPE_VARIANTS = 20


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def paths(root: Path = ROOT) -> tuple[Path, Path, Path, Path]:
    return (
        root / "timetable.db",
        root / ".timetable.db.upload",
        root / "timetable.db.previous",
        root / ".timetable.db.failed",
    )


def _validate_schema(connection: sqlite3.Connection, *,
                     require_stop_routes: bool = False) -> set[str]:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing_tables = sorted(set(REQUIRED_COLUMNS) - tables)
    if missing_tables:
        raise RuntimeError(
            "missing required timetable tables: " + ", ".join(missing_tables))
    columns = dict(REQUIRED_COLUMNS)
    if "stop_routes" in tables:
        columns["stop_routes"] = STOP_ROUTES_COLUMNS
    elif require_stop_routes:
        raise RuntimeError("missing required timetable tables: stop_routes")
    for table, required in columns.items():
        actual = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        missing = sorted(required - actual)
        if missing:
            raise RuntimeError(
                f"table {table} is missing columns: {', '.join(missing)}")
    indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")
    }
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise RuntimeError(
            "missing required timetable indexes: " + ", ".join(missing_indexes))
    return tables


def _validate_shape_geometry(connection: sqlite3.Connection) -> int:
    count = 0
    for route, noc, direction, variant, raw in connection.execute(
            "SELECT route_name, operator_noc, direction_id, variant, points_json "
            "FROM route_shapes"):
        count += 1
        try:
            points = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"invalid route shape JSON for {noc}/{route}/{direction}/{variant}") \
                from exc
        if not isinstance(points, list) or len(points) < 2:
            raise RuntimeError(
                f"route shape has fewer than 2 points: "
                f"{noc}/{route}/{direction}/{variant}")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise RuntimeError(
                    f"invalid route shape point: {noc}/{route}/{direction}/{variant}")
            lat, lon = point
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) \
                    or not math.isfinite(lat) or not math.isfinite(lon) \
                    or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise RuntimeError(
                    f"out-of-range route shape point: "
                    f"{noc}/{route}/{direction}/{variant}")
    return count


def validate(path: Path, *, today: date | None = None,
             minimum_service_days: int = 0,
             require_stop_routes: bool = False) -> dict[str, object]:
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
        tables = _validate_schema(
            connection, require_stop_routes=require_stop_routes)
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in REQUIRED_COLUMNS
        }
        if "stop_routes" in tables:
            counts["stop_routes"] = connection.execute(
                "SELECT COUNT(*) FROM stop_routes").fetchone()[0]
        for table in ("agency", "routes", "stops", "trips", "stop_times"):
            if counts[table] <= 0:
                raise RuntimeError(f"timetable table {table} is empty")
        if counts["route_shapes"] <= 0:
            raise RuntimeError("timetable contains no route shapes")
        if "stop_routes" in tables:
            if counts["stop_routes"] <= 0:
                raise RuntimeError("timetable contains no precomputed stop routes")
            invalid_stop_route = connection.execute("""
                SELECT sr.stop_code, sr.route_short_name
                FROM stop_routes sr
                WHERE sr.stop_code = '' OR sr.route_short_name = ''
                   OR NOT EXISTS (
                       SELECT 1 FROM stops s
                       WHERE s.stop_code = sr.stop_code)
                   OR NOT EXISTS (
                       SELECT 1 FROM routes r
                       WHERE r.route_short_name = sr.route_short_name)
                LIMIT 1
            """).fetchone()
            if invalid_stop_route:
                raise RuntimeError(
                    "invalid precomputed stop route: "
                    f"{invalid_stop_route[0]!r}/{invalid_stop_route[1]!r}")
            missing_stop_route = connection.execute("""
                SELECT s.stop_code
                FROM stops s
                WHERE s.stop_code IS NOT NULL AND s.stop_code != ''
                  AND NOT EXISTS (
                      SELECT 1 FROM stop_routes sr
                      WHERE sr.stop_code = s.stop_code)
                LIMIT 1
            """).fetchone()
            if missing_stop_route:
                raise RuntimeError(
                    "precomputed stop routes missing stop code: "
                    f"{missing_stop_route[0]!r}")
        routes = {row[0] for row in connection.execute(
            "SELECT DISTINCT r.route_short_name FROM routes r "
            "JOIN agency a ON r.agency_id=a.agency_id WHERE a.agency_noc='FBRI'")}
        latest = max((row[0] for row in connection.execute(
            "SELECT MAX(end_date) FROM calendar UNION ALL "
            "SELECT MAX(date) FROM calendar_dates WHERE exception_type=1") if row[0]),
            default=None)
        duplicate = connection.execute(
            "SELECT trip_id, stop_sequence, COUNT(*) FROM stop_times "
            "GROUP BY trip_id, stop_sequence HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate:
            raise RuntimeError(
                "duplicate stop_times for "
                f"trip_id={duplicate[0]!r}, stop_sequence={duplicate[1]!r}")
        timetable_shape_keys = set(connection.execute(
            "SELECT DISTINCT r.route_short_name, a.agency_noc, "
            "COALESCE(t.direction_id, 0) FROM trips t "
            "JOIN routes r ON t.route_id=r.route_id "
            "JOIN agency a ON r.agency_id=a.agency_id "
            "WHERE t.shape_id IS NOT NULL AND t.shape_id != ''"))
        route_shape_keys = set(connection.execute(
            "SELECT DISTINCT route_name, operator_noc, direction_id "
            "FROM route_shapes"))
        if timetable_shape_keys != route_shape_keys:
            missing = len(timetable_shape_keys - route_shape_keys)
            unexpected = len(route_shape_keys - timetable_shape_keys)
            raise RuntimeError(
                "route shape key mismatch: "
                f"missing={missing}, unexpected={unexpected}")
        over_variant = connection.execute(
            "SELECT route_name, operator_noc, direction_id, COUNT(*) "
            "FROM route_shapes GROUP BY route_name, operator_noc, direction_id "
            "HAVING COUNT(*) > ? LIMIT 1", (MAX_SHAPE_VARIANTS,)
        ).fetchone()
        if over_variant:
            raise RuntimeError(
                "route shape variant cap exceeded: "
                f"{over_variant[1]}/{over_variant[0]}/{over_variant[2]} "
                f"has {over_variant[3]}")
        shape_count = _validate_shape_geometry(connection)
        edition_result = validate_route_editions(
            connection, require_table=require_stop_routes)
    finally:
        connection.close()
    missing = sorted(EXPECTED_FBRI - routes)
    minimum_date = (today or date.today()) + timedelta(days=minimum_service_days)
    minimum_text = minimum_date.strftime("%Y%m%d")
    if missing:
        raise RuntimeError(f"missing required First routes: {', '.join(missing)}")
    if latest is None or latest < minimum_text:
        raise RuntimeError(
            "timetable service window is stale/too short: "
            f"latest={latest or 'missing'}, required={minimum_text}")
    if shape_count <= 0:
        raise RuntimeError("timetable contains no route shapes")
    result = {
        "latest_service": latest,
        "first_routes": len(routes),
        "route_shapes": shape_count,
        "routes": counts["routes"],
        "trips": counts["trips"],
        "stops": counts["stops"],
        "stop_times": counts["stop_times"],
    }
    if "stop_routes" in counts:
        result["stop_routes"] = counts["stop_routes"]
    result.update(edition_result)
    return result


def promote(root: Path = ROOT) -> dict[str, object]:
    live, upload, previous, _ = paths(root)
    result = validate(upload)
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    previous.unlink(missing_ok=True)
    if live.exists():
        os.link(live, previous)
    os.chmod(upload, 0o600)
    os.replace(upload, live)
    _sync_directory(root)
    return result


def rollback(root: Path = ROOT) -> dict[str, object]:
    live, _, previous, failed = paths(root)
    if not previous.is_file() or previous.is_symlink():
        raise RuntimeError("no safe timetable rollback copy exists")
    failed.unlink(missing_ok=True)
    if live.exists():
        os.replace(live, failed)
    os.replace(previous, live)
    _sync_directory(root)
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
