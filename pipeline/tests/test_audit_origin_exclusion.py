from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402


def test_route_rollup_excludes_origin_but_keeps_trip_coverage():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE timepoint_observations (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            stop_sequence INTEGER, stop_code TEXT, scheduled_local TEXT,
            observed_delay_s INTEGER, on_time INTEGER, gps_distance_m INTEGER,
            recorded_at TEXT, vehicle_ref TEXT,
            is_origin INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE expected_trips (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            first_departure TEXT
        );
    """)
    connection.executemany(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?)", [
            ("20260815", "FBRI", "75", "trip-1", "10:00:00"),
            ("20260815", "FBRI", "75", "trip-2", "10:30:00"),
        ])
    connection.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("20260815", "FBRI", "75", "trip-1", 0, "ORIGIN",
             "2026-08-15T10:00:00+01:00", -900, 0, 0,
             "2026-08-15T08:45:00+00:00", "FBRI-1", 1),
            ("20260815", "FBRI", "75", "trip-1", 2, "LATER",
             "2026-08-15T10:10:00+01:00", 0, 1, 10,
             "2026-08-15T09:10:00+00:00", "FBRI-1", 0),
        ])
    audit_rollup.init_summary_tables(connection)

    result = audit_rollup.rollup(
        connection, "20260815", ["FBRI"], "FBRI")

    assert result["readings_total"] == 1
    assert result["in_gate"] == 1
    assert result["on_time_pct"] == 100.0
    # Origin evidence still proves the trip appeared; it is excluded only
    # from the punctuality numerator/denominator.
    assert result["observed"] == 1
    assert result["expected"] == 2
