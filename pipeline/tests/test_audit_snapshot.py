from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_snapshot  # noqa: E402


def test_snapshot_uses_each_trips_actual_first_stop(monkeypatch, tmp_path):
    timetable = tmp_path / "timetable.db"
    audit = tmp_path / "audit.db"
    connection = sqlite3.connect(timetable)
    connection.executescript("""
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT, route_id TEXT, service_id TEXT, direction_id INTEGER);
        CREATE TABLE stop_times (
            trip_id TEXT, departure_time TEXT, stop_sequence INTEGER);
        CREATE TABLE calendar (
            service_id TEXT, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        INSERT INTO agency VALUES ('A', 'FBRI');
        INSERT INTO routes VALUES ('R', 'A', '75');
        INSERT INTO calendar VALUES (
            'WK', 1, 1, 1, 1, 1, 0, 0, '20260101', '20261231');
        INSERT INTO trips VALUES ('ZERO', 'R', 'WK', 0);
        INSERT INTO trips VALUES ('ONE', 'R', 'WK', 1);
        INSERT INTO stop_times VALUES ('ZERO', '08:00:00', 0);
        INSERT INTO stop_times VALUES ('ZERO', '08:30:00', 1);
        INSERT INTO stop_times VALUES ('ONE', '09:00:00', 1);
        INSERT INTO stop_times VALUES ('ONE', '09:30:00', 2);
    """)
    connection.commit()
    connection.close()
    monkeypatch.setattr(audit_snapshot, "TIMETABLE_DB", str(timetable))
    monkeypatch.setattr(audit_snapshot, "AUDIT_DB", str(audit))

    assert audit_snapshot.build_snapshot("20260814") == 2

    result = sqlite3.connect(audit).execute(
        "SELECT trip_id, first_departure, siri_ref "
        "FROM expected_trips ORDER BY trip_id").fetchall()
    assert result == [
        ("ONE", "09:00:00", "0900"),
        ("ZERO", "08:00:00", "0800"),
    ]
