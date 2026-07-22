import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timetable_control import EXPECTED_FBRI, paths, promote, rollback, validate


def make_timetable(path: Path, *, routes=EXPECTED_FBRI,
                   latest="20991231", shapes=1) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE agency (
            agency_id TEXT PRIMARY KEY, agency_name TEXT, agency_url TEXT,
            agency_timezone TEXT, agency_lang TEXT, agency_phone TEXT,
            agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT,
            route_long_name TEXT, route_type INTEGER);
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT,
            stop_lat REAL, stop_lon REAL, wheelchair_boarding INTEGER,
            location_type INTEGER, parent_station TEXT, platform_code TEXT);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
            trip_headsign TEXT, trip_short_name TEXT, direction_id INTEGER,
            block_id TEXT, shape_id TEXT, wheelchair_accessible INTEGER,
            vehicle_journey_code TEXT);
        CREATE TABLE stop_times (
            trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT,
            stop_sequence INTEGER, stop_headsign TEXT, pickup_type INTEGER,
            drop_off_type INTEGER, shape_dist_traveled REAL, timepoint INTEGER);
        CREATE TABLE calendar (
            service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        CREATE TABLE route_shapes (
            route_name TEXT, operator_noc TEXT, direction_id INTEGER,
            variant INTEGER, points_json TEXT,
            PRIMARY KEY (route_name, operator_noc, direction_id, variant));
        CREATE TABLE stop_routes (
            stop_code TEXT NOT NULL, route_short_name TEXT NOT NULL,
            PRIMARY KEY (stop_code, route_short_name)) WITHOUT ROWID;

        CREATE INDEX idx_trips_vjc ON trips(vehicle_journey_code);
        CREATE INDEX idx_routes_agency ON routes(agency_id);
        CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        CREATE INDEX idx_stop_times_trip_seq ON stop_times(trip_id, stop_sequence);
        CREATE INDEX idx_trips_route_dir ON trips(route_id, direction_id);
        CREATE INDEX idx_trips_service ON trips(service_id);
        CREATE INDEX idx_routes_short_name ON routes(route_short_name);
        CREATE INDEX idx_calendar_dates_service ON calendar_dates(service_id);
        CREATE INDEX idx_calendar_dates_date ON calendar_dates(date);
        CREATE INDEX idx_stops_code ON stops(stop_code);
        CREATE INDEX idx_stops_latlon ON stops(stop_lat, stop_lon);
        CREATE INDEX idx_agency_noc ON agency(agency_noc);

        INSERT INTO agency (agency_id, agency_name, agency_noc)
            VALUES ('first', 'First Bristol', 'FBRI');
        INSERT INTO stops (stop_id, stop_code, stop_name, stop_lat, stop_lon)
            VALUES ('S1', '0100S1', 'Test stop', 51.45, -2.59);
    """)
    sorted_routes = sorted(routes)
    connection.executemany(
        "INSERT INTO routes (route_id, agency_id, route_short_name, route_type) "
        "VALUES (?, 'first', ?, 3)",
        [(f"R{index}", route) for index, route in enumerate(sorted_routes)])
    first_route = "R0"
    first_name = sorted_routes[0]
    connection.execute(
        "INSERT INTO trips (trip_id, route_id, service_id, direction_id, shape_id) "
        "VALUES ('T1', ?, 'WK', 0, 'SH1')", (first_route,))
    connection.execute(
        "INSERT INTO stop_times (trip_id, arrival_time, departure_time, stop_id, "
        "stop_sequence) VALUES ('T1', '08:00:00', '08:00:00', 'S1', 1)")
    connection.execute(
        "INSERT INTO stop_routes VALUES ('0100S1', ?)", (first_name,))
    connection.execute(
        "INSERT INTO calendar (service_id, monday, tuesday, wednesday, thursday, "
        "friday, saturday, sunday, start_date, end_date) "
        "VALUES ('WK', 1, 1, 1, 1, 1, 1, 1, '20200101', ?)", (latest,))
    connection.executemany(
        "INSERT INTO route_shapes VALUES (?, 'FBRI', 0, ?, ?)",
        [(first_name, index, "[[51.45, -2.59], [51.46, -2.58]]")
         for index in range(shapes)])
    connection.commit()
    assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    connection.close()


def test_validate_promote_and_rollback(tmp_path):
    live, upload, previous, failed = paths(tmp_path)
    make_timetable(live, latest="20980101")
    make_timetable(upload, latest="20991231")

    result = promote(tmp_path)
    assert result["latest_service"] == "20991231"
    assert validate(live)["latest_service"] == "20991231"
    assert previous.is_file()

    result = rollback(tmp_path)
    assert result["latest_service"] == "20980101"
    assert failed.is_file()


def test_new_candidates_require_precomputed_stop_routes_but_legacy_live_is_readable(
        tmp_path):
    legacy = tmp_path / "legacy.db"
    make_timetable(legacy)
    connection = sqlite3.connect(legacy)
    connection.execute("DROP TABLE stop_routes")
    connection.commit()
    connection.close()

    assert "stop_routes" not in validate(legacy)
    with pytest.raises(RuntimeError, match="stop_routes"):
        validate(legacy, require_stop_routes=True)


def test_rejects_incomplete_precomputed_stop_routes(tmp_path):
    database = tmp_path / "bad-stop-routes.db"
    make_timetable(database)
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM stop_routes")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="no precomputed stop routes"):
        validate(database, require_stop_routes=True)


def test_rejects_missing_routes_stale_service_and_missing_shapes(tmp_path):
    missing = tmp_path / "missing.db"
    make_timetable(missing, routes={"1"})
    with pytest.raises(RuntimeError, match="missing required First routes"):
        validate(missing)

    stale = tmp_path / "stale.db"
    make_timetable(stale, latest="20200101")
    with pytest.raises(RuntimeError, match="stale"):
        validate(stale, today=date(2026, 7, 17))

    empty = tmp_path / "empty.db"
    make_timetable(empty, shapes=0)
    with pytest.raises(RuntimeError, match="no route shapes"):
        validate(empty)


def test_rejects_duplicate_stop_times_and_shape_key_mismatch(tmp_path):
    duplicate = tmp_path / "duplicate.db"
    make_timetable(duplicate)
    connection = sqlite3.connect(duplicate)
    connection.execute(
        "INSERT INTO stop_times (trip_id, stop_id, stop_sequence) "
        "VALUES ('T1', 'S1', 1)")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="duplicate stop_times"):
        validate(duplicate)

    mismatch = tmp_path / "mismatch.db"
    make_timetable(mismatch)
    connection = sqlite3.connect(mismatch)
    connection.execute("UPDATE route_shapes SET route_name='not-the-trip-route'")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="route shape key mismatch"):
        validate(mismatch)


def test_rejects_bad_shape_geometry_and_missing_index(tmp_path):
    geometry = tmp_path / "geometry.db"
    make_timetable(geometry)
    connection = sqlite3.connect(geometry)
    connection.execute("UPDATE route_shapes SET points_json='[[999, 0], [1, 2]]'")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="out-of-range route shape point"):
        validate(geometry)

    missing_index = tmp_path / "missing-index.db"
    make_timetable(missing_index)
    connection = sqlite3.connect(missing_index)
    connection.execute("DROP INDEX idx_stop_times_trip_seq")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="missing required timetable indexes"):
        validate(missing_index)


def test_can_require_a_minimum_future_service_window(tmp_path):
    path = tmp_path / "short-window.db"
    make_timetable(path, latest="20260720")
    assert validate(path, today=date(2026, 7, 17), minimum_service_days=3)
    with pytest.raises(RuntimeError, match="stale/too short"):
        validate(path, today=date(2026, 7, 17), minimum_service_days=4)
