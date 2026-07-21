import json
import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from timetable_manifest import (
    GTFS_OPTIONAL,
    GTFS_REQUIRED,
    create_manifest,
    verify_manifest,
)


def make_database(path: Path) -> None:
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
            VALUES ('A', 'First', 'FBRI');
        INSERT INTO stops (stop_id, stop_code, stop_name, stop_lat, stop_lon)
            VALUES ('S', '0100S', 'Stop', 51.45, -2.59);
        INSERT INTO trips (
            trip_id, route_id, service_id, direction_id, shape_id)
            VALUES ('T', 'R0', 'WK', 0, 'SH');
        INSERT INTO stop_times (
            trip_id, arrival_time, departure_time, stop_id, stop_sequence)
            VALUES ('T', '08:00:00', '08:00:00', 'S', 1);
        INSERT INTO calendar (
            service_id, monday, tuesday, wednesday, thursday, friday,
            saturday, sunday, start_date, end_date)
            VALUES ('WK', 1, 1, 1, 1, 1, 1, 1, '20200101', '20991231');
    """)
    routes = ["1", "2", "42", "43", "44", "45", "75", "76", "X1", "m1"]
    connection.executemany(
        "INSERT INTO routes (route_id, agency_id, route_short_name, route_type) "
        "VALUES (?, 'A', ?, 3)",
        [(f"R{index}", route) for index, route in enumerate(routes)])
    connection.execute(
        "INSERT INTO route_shapes VALUES "
        "('1', 'FBRI', 0, 0, '[[51.45, -2.59], [51.46, -2.58]]')")
    connection.commit()
    connection.close()


def make_sources(root: Path) -> tuple[Path, Path, Path]:
    gtfs = root / "gtfs"
    first = root / "first"
    tnds = root / "tnds"
    gtfs.mkdir()
    first.mkdir()
    tnds.mkdir()
    for name in (*GTFS_REQUIRED, *GTFS_OPTIONAL):
        (gtfs / name).write_text(f"fixture {name}\n", encoding="utf-8")
    (first / "first.zip").write_bytes(b"fixture-first")
    (tnds / "SW.zip").write_bytes(b"fixture-tnds")
    return gtfs, first, tnds


def test_create_and_verify_manifest(tmp_path):
    database = tmp_path / "timetable.db"
    manifest_path = tmp_path / "manifest.json"
    make_database(database)
    gtfs, first, tnds = make_sources(tmp_path)

    manifest = create_manifest(
        database=database,
        output=manifest_path,
        gtfs=gtfs,
        first_txc=first,
        tnds=tnds,
        builder_commit="a" * 40,
        workflow_run_id="123",
        minimum_service_days=14,
    )

    assert manifest["manifest_version"] == 1
    assert manifest["artifact"]["filename"] == "timetable.db"
    assert manifest["database"]["timetable_shape_keys"] == \
        manifest["database"]["route_shape_keys"]
    assert verify_manifest(
        database=database, manifest_path=manifest_path)["route_shapes"] == 1


def test_verify_rejects_manifest_hash_mismatch(tmp_path):
    database = tmp_path / "timetable.db"
    manifest_path = tmp_path / "manifest.json"
    make_database(database)
    gtfs, first, tnds = make_sources(tmp_path)
    create_manifest(
        database=database,
        output=manifest_path,
        gtfs=gtfs,
        first_txc=first,
        tnds=tnds,
        builder_commit="a" * 40,
        workflow_run_id="123",
        minimum_service_days=0,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["artifact"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="SHA-256"):
        verify_manifest(database=database, manifest_path=manifest_path)


def test_create_rejects_missing_required_source(tmp_path):
    database = tmp_path / "timetable.db"
    make_database(database)
    gtfs, first, tnds = make_sources(tmp_path)
    (gtfs / "shapes.txt").unlink()

    with pytest.raises(RuntimeError, match="required GTFS source"):
        create_manifest(
            database=database,
            output=tmp_path / "manifest.json",
            gtfs=gtfs,
            first_txc=first,
            tnds=tnds,
            builder_commit="a" * 40,
            workflow_run_id="123",
            minimum_service_days=0,
        )
