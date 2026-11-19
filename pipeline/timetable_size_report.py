"""Where a timetable's size goes (planning #130).

Reports, per operator, how many trips and stop-time rows a timetable holds,
how many sit on services that never run, and how many are exact repeats of a
trip already present in another edition (same operator, route, direction and
every stop and time). The repeats are what grew the timetable past 512 MiB in
November 2026: First publishes a weekly edition series and BODS keeps each one
in full for nine months. Reporting only; nothing here changes the timetable.

    python pipeline/timetable_size_report.py C:/dev/bbb-research/timetable.db
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _active_services(connection: sqlite3.Connection) -> set[str]:
    """Services with at least one running day."""
    calendars = {row[0]: row[1:] for row in connection.execute(
        f"SELECT service_id, {', '.join(WEEKDAYS)}, start_date, end_date FROM calendar")}
    exceptions: dict[str, dict[str, int]] = defaultdict(dict)
    for service, day, kind in connection.execute(
            "SELECT service_id, date, exception_type FROM calendar_dates"):
        exceptions[service][str(day)] = int(kind)
    active = set()
    for service in set(calendars) | set(exceptions):
        if any(kind == 1 for kind in exceptions[service].values()):
            active.add(service)
            continue
        row = calendars.get(service)
        if not row:
            continue
        day = datetime.strptime(str(row[7]), "%Y%m%d").date()
        end = datetime.strptime(str(row[8]), "%Y%m%d").date()
        while day <= end:
            if row[day.weekday()] and exceptions[service].get(day.strftime("%Y%m%d")) != 2:
                active.add(service)
                break
            day += timedelta(days=1)
    return active


def size_report(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        active = _active_services(connection)
        trips = {row[0]: row[1:] for row in connection.execute(
            """SELECT t.trip_id, a.agency_noc, r.route_short_name, t.direction_id, t.service_id
                 FROM trips t JOIN routes r USING(route_id) JOIN agency a USING(agency_id)""")}
        signatures: dict[str, "hashlib._Hash"] = defaultdict(hashlib.sha1)
        rows: dict[str, int] = defaultdict(int)
        for trip, stop, arrival, departure, sequence in connection.execute(
                "SELECT trip_id, stop_id, arrival_time, departure_time, stop_sequence "
                "FROM stop_times ORDER BY trip_id, stop_sequence"):
            signatures[trip].update(f"{stop}|{arrival}|{departure}|{sequence};".encode())
            rows[trip] += 1
    finally:
        connection.close()
    seen: set[tuple] = set()
    operators: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for trip, (operator, route, direction, service) in sorted(trips.items()):
        counts = operators[str(operator)]
        counts["trips"] += 1
        counts["stop_times"] += rows.get(trip, 0)
        if service not in active:
            counts["idle_trips"] += 1
            counts["idle_stop_times"] += rows.get(trip, 0)
        key = (operator, route, direction, signatures[trip].hexdigest())
        if key in seen:
            counts["repeated_trips"] += 1
            counts["repeated_stop_times"] += rows.get(trip, 0)
        seen.add(key)
    total: dict[str, int] = defaultdict(int)
    for counts in operators.values():
        for name, value in counts.items():
            total[name] += value
    return {
        "bytes": database.stat().st_size,
        "total": dict(total),
        "operators": {name: dict(counts) for name, counts in sorted(
            operators.items(), key=lambda item: -item[1]["stop_times"])},
    }


def summary_line(report: dict[str, object]) -> str:
    total = report["total"]
    stop_times = max(1, total.get("stop_times", 0))
    return (f"{report['bytes'] / 1e6:.0f} MB, {total.get('trips', 0):,} trips, "
            f"{total.get('stop_times', 0):,} stop times; "
            f"{100 * total.get('repeated_stop_times', 0) / stop_times:.0f}% of stop times "
            f"repeat a trip from another edition, "
            f"{100 * total.get('idle_stop_times', 0) / stop_times:.0f}% are on services that never run")


if __name__ == "__main__":
    report = size_report(Path(sys.argv[1]))
    print(summary_line(report))
    print(json.dumps(report, indent=2))
