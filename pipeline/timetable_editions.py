#!/usr/bin/env python3
"""Resolve overlapping BODS timetable editions for one registered route.

BODS regional GTFS exports can contain the current and one or more future
versions of the same TransXChange service under a single ``route_id``.  Their
calendar ranges sometimes overlap instead of ending when the next version
starts.  That makes multiple versions look active at once.

This module preserves every edition, but gives replacement-like editions
non-overlapping effective windows.  Small or differently scheduled cohorts
are retained in parallel because they may be genuine school, event, or
day-specific additions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import sqlite3
from pathlib import Path


TABLE = "route_service_editions"
DAY_COLUMNS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)
EDITION_COLUMNS = {
    "route_id", "edition_start", "source_end", "effective_end",
    "trip_count", "day_mask", "resolution", "superseded_by",
}
MIN_REPLACEMENT_RATIO = 0.25
MAX_REPLACEMENT_RATIO = 4.0


@dataclass
class Edition:
    route_id: str
    start: str
    source_end: str
    trip_count: int
    day_mask: str
    effective_end: str = ""
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if not self.effective_end:
            self.effective_end = self.source_end

    @property
    def resolution(self) -> str:
        return "superseded" if self.superseded_by else "retained"


def _date(value: str, field: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid {field} in timetable calendar: {value!r}") from exc


def _day_before(value: str) -> str:
    return (_date(value, "edition start") - timedelta(days=1)).strftime("%Y%m%d")


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }


def _editions(connection: sqlite3.Connection) -> list[Edition]:
    day_sql = ", ".join(f"MAX(COALESCE(c.{day}, 0))" for day in DAY_COLUMNS)
    rows = connection.execute(f"""
        SELECT t.route_id, c.start_date, MAX(c.end_date), COUNT(DISTINCT t.trip_id),
               {day_sql}
          FROM trips t
          JOIN calendar c ON c.service_id=t.service_id
         WHERE c.start_date IS NOT NULL AND c.start_date != ''
           AND c.end_date IS NOT NULL AND c.end_date != ''
         GROUP BY t.route_id, c.start_date
         ORDER BY t.route_id, c.start_date
    """).fetchall()
    result = []
    for route_id, start, end, trip_count, *days in rows:
        _date(start, "edition start")
        _date(end, "edition end")
        if end < start:
            raise RuntimeError(
                f"calendar edition ends before it starts: {route_id}/{start}/{end}")
        result.append(Edition(
            route_id=str(route_id),
            start=str(start),
            source_end=str(end),
            trip_count=int(trip_count),
            day_mask="".join("1" if int(value or 0) else "0" for value in days),
        ))
    return result


def _replacement_like(older: Edition, newer: Edition) -> bool:
    if older.route_id != newer.route_id or newer.start <= older.start:
        return False
    if older.source_end < newer.start or older.day_mask != newer.day_mask:
        return False
    ratio = newer.trip_count / older.trip_count
    return MIN_REPLACEMENT_RATIO <= ratio <= MAX_REPLACEMENT_RATIO


def _resolve(editions: list[Edition]) -> None:
    by_route: dict[str, list[Edition]] = {}
    for edition in editions:
        by_route.setdefault(edition.route_id, []).append(edition)
    for route_editions in by_route.values():
        for index, older in enumerate(route_editions[:-1]):
            replacement = next(
                (newer for newer in route_editions[index + 1:]
                 if _replacement_like(older, newer)),
                None,
            )
            if replacement is None:
                continue
            older.effective_end = min(older.source_end, _day_before(replacement.start))
            older.superseded_by = replacement.start


def _clone_service_id(route_id: str, service_id: str, end_date: str) -> str:
    raw = f"{route_id}\0{service_id}\0{end_date}".encode("utf-8")
    return "BBBWIN_" + hashlib.sha256(raw).hexdigest()[:24]


def _create_table(connection: sqlite3.Connection) -> None:
    connection.execute(f"""
        CREATE TABLE {TABLE} (
            route_id TEXT NOT NULL,
            edition_start TEXT NOT NULL,
            source_end TEXT NOT NULL,
            effective_end TEXT NOT NULL,
            trip_count INTEGER NOT NULL,
            day_mask TEXT NOT NULL,
            resolution TEXT NOT NULL,
            superseded_by TEXT,
            PRIMARY KEY (route_id, edition_start)
        ) WITHOUT ROWID
    """)


def _apply_cutoff(connection: sqlite3.Connection, edition: Edition) -> int:
    if edition.resolution != "superseded":
        return 0
    services = connection.execute("""
        SELECT DISTINCT c.service_id, c.monday, c.tuesday, c.wednesday,
               c.thursday, c.friday, c.saturday, c.sunday,
               c.start_date, c.end_date
          FROM trips t
          JOIN calendar c ON c.service_id=t.service_id
         WHERE t.route_id=? AND c.start_date=? AND c.end_date>?
    """, (edition.route_id, edition.start, edition.effective_end)).fetchall()
    changed = 0
    for row in services:
        service_id = str(row[0])
        clone_id = _clone_service_id(
            edition.route_id, service_id, edition.effective_end)
        if connection.execute(
                "SELECT 1 FROM calendar WHERE service_id=?", (clone_id,)).fetchone():
            raise RuntimeError(f"generated calendar id already exists: {clone_id}")
        connection.execute("""
            INSERT INTO calendar (
                service_id, monday, tuesday, wednesday, thursday, friday,
                saturday, sunday, start_date, end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (clone_id, *row[1:9], edition.effective_end))
        connection.execute("""
            INSERT INTO calendar_dates (service_id, date, exception_type)
            SELECT ?, date, exception_type
              FROM calendar_dates
             WHERE service_id=? AND date>=? AND date<=?
        """, (clone_id, service_id, edition.start, edition.effective_end))
        cursor = connection.execute("""
            UPDATE trips SET service_id=?
             WHERE route_id=? AND service_id=?
        """, (clone_id, edition.route_id, service_id))
        changed += cursor.rowcount
    return changed


def normalize_database(path: Path) -> dict[str, int]:
    """Apply route-edition windows transactionally to a disposable database."""
    connection = sqlite3.connect(path)
    try:
        if TABLE in _tables(connection):
            result = validate_database(connection, require_table=True)
            result["trips_rewindowed"] = 0
            return result
        editions = _editions(connection)
        if not editions:
            raise RuntimeError("timetable contains no calendar-backed route editions")
        _resolve(editions)
        connection.execute("BEGIN IMMEDIATE")
        _create_table(connection)
        trips_rewindowed = sum(
            _apply_cutoff(connection, edition) for edition in editions)
        connection.executemany(f"""
            INSERT INTO {TABLE} (
                route_id, edition_start, source_end, effective_end,
                trip_count, day_mask, resolution, superseded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (edition.route_id, edition.start, edition.source_end,
             edition.effective_end, edition.trip_count, edition.day_mask,
             edition.resolution, edition.superseded_by)
            for edition in editions
        ])
        connection.commit()
        result = validate_database(connection, require_table=True)
        result["trips_rewindowed"] = trips_rewindowed
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def validate_database(connection: sqlite3.Connection, *,
                      require_table: bool = False) -> dict[str, int]:
    """Independently check the recorded windows and remaining overlaps."""
    if TABLE not in _tables(connection):
        if require_table:
            raise RuntimeError(f"missing required timetable table: {TABLE}")
        return {}
    columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")
    }
    missing = sorted(EDITION_COLUMNS - columns)
    if missing:
        raise RuntimeError(
            f"table {TABLE} is missing columns: {', '.join(missing)}")

    records = {
        (str(row[0]), str(row[1])): row
        for row in connection.execute(f"""
            SELECT route_id, edition_start, source_end, effective_end,
                   trip_count, day_mask, resolution, superseded_by
              FROM {TABLE}
        """)
    }
    actual = _editions(connection)
    actual_keys = {(edition.route_id, edition.start) for edition in actual}
    if set(records) != actual_keys:
        raise RuntimeError("route-edition record set does not match timetable trips")

    superseded = 0
    for edition in actual:
        row = records[(edition.route_id, edition.start)]
        _, _, source_end, effective_end, trip_count, day_mask, resolution, by = row
        _date(str(source_end), "source edition end")
        _date(str(effective_end), "effective edition end")
        if str(effective_end) != edition.source_end \
                or int(trip_count) != edition.trip_count \
                or str(day_mask) != edition.day_mask:
            raise RuntimeError(
                f"route-edition record differs from timetable: "
                f"{edition.route_id}/{edition.start}")
        if resolution == "superseded":
            superseded += 1
            if not by or str(effective_end) != _day_before(str(by)) \
                    or str(source_end) < str(by):
                raise RuntimeError(
                    f"invalid superseded route edition: "
                    f"{edition.route_id}/{edition.start}")
        elif resolution == "retained":
            if by is not None or str(source_end) != str(effective_end):
                raise RuntimeError(
                    f"invalid retained route edition: "
                    f"{edition.route_id}/{edition.start}")
        else:
            raise RuntimeError(f"invalid route-edition resolution: {resolution!r}")

    by_route: dict[str, list[Edition]] = {}
    for edition in actual:
        by_route.setdefault(edition.route_id, []).append(edition)
    for route_editions in by_route.values():
        for index, older in enumerate(route_editions[:-1]):
            for newer in route_editions[index + 1:]:
                if _replacement_like(older, newer):
                    raise RuntimeError(
                        "unresolved overlapping replacement editions: "
                        f"{older.route_id}/{older.start}/{newer.start}")

    return {
        "route_editions": len(actual),
        "superseded_route_editions": superseded,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(normalize_database(args.database))
