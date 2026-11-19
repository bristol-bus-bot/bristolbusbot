"""#130: where a timetable's size goes."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import timetable_size_report  # noqa: E402

CALLS = (("A", "08:00:00"), ("B", "08:10:00"))


def build(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
            CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
            CREATE TABLE trips (trip_id TEXT, route_id TEXT, service_id TEXT, direction_id INTEGER);
            CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, arrival_time TEXT,
                                     departure_time TEXT, stop_sequence INTEGER);
            CREATE TABLE calendar (service_id TEXT, monday INT, tuesday INT, wednesday INT,
                                   thursday INT, friday INT, saturday INT, sunday INT,
                                   start_date TEXT, end_date TEXT);
            CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INT);
            INSERT INTO agency VALUES ('A1', 'FBRI');
            INSERT INTO routes VALUES ('R1', 'A1', '75');
            INSERT INTO calendar VALUES ('w39', 1,1,1,1,1,0,0, '20261221', '20261225');
            INSERT INTO calendar VALUES ('w40', 1,1,1,1,1,0,0, '20261228', '20270101');
            INSERT INTO calendar VALUES ('never', 0,0,0,0,0,1,0, '20261221', '20261225');
            INSERT INTO calendar VALUES ('extra', 0,0,0,0,0,0,0, '20261221', '20261221');
            INSERT INTO calendar_dates VALUES ('extra', '20261224', 1);
        """)
        trips = [("t39", "w39", CALLS), ("t40", "w40", CALLS),            # same journey, two editions
                 ("t40b", "w40", (("A", "09:00:00"), ("B", "09:10:00"))),
                 ("idle", "never", (("A", "10:00:00"), ("B", "10:10:00"))),
                 ("added", "extra", (("A", "11:00:00"), ("B", "11:10:00")))]
        for trip, service, calls in trips:
            connection.execute("INSERT INTO trips VALUES (?, 'R1', ?, 0)", (trip, service))
            for sequence, (stop, time) in enumerate(calls):
                connection.execute("INSERT INTO stop_times VALUES (?,?,?,?,?)",
                                   (trip, stop, time, time, sequence))


def test_repeated_and_idle_trips_are_counted(tmp_path):
    path = tmp_path / "timetable.db"
    build(path)
    report = timetable_size_report.size_report(path)
    assert report["total"] == {"trips": 5, "stop_times": 10, "repeated_trips": 1,
                               "repeated_stop_times": 2, "idle_trips": 1, "idle_stop_times": 2}
    assert report["bytes"] == path.stat().st_size
    assert "20% of stop times repeat a trip from another edition" in \
        timetable_size_report.summary_line(report)
