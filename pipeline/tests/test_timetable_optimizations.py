import csv
import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import build_timetable_weca as weca
import import_shapes


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_route_scan_includes_all_trips_without_regional_mapping(tmp_path):
    stop_times = tmp_path / "stop_times.txt"
    trips = tmp_path / "trips.txt"
    write_csv(stop_times, ["trip_id", "stop_id"], [
        {"trip_id": "T1", "stop_id": "S1"},
        {"trip_id": "T3", "stop_id": "S9"},
    ])
    write_csv(trips, ["trip_id", "route_id"], [
        {"trip_id": "T1", "route_id": "R1"},
        {"trip_id": "T2", "route_id": "R1"},
        {"trip_id": "T3", "route_id": "R2"},
    ])

    route_ids, trip_ids = weca.find_routes_serving_area(
        stop_times, trips, {"S1"})

    assert route_ids == {"R1"}
    assert trip_ids == {"T1", "T2"}


def test_streaming_csv_loader_treats_an_empty_filter_as_empty(tmp_path):
    path = tmp_path / "agency.txt"
    write_csv(path, ["agency_id", "agency_name", "agency_noc"], [
        {"agency_id": "A", "agency_name": "One", "agency_noc": "ONE"},
        {"agency_id": "B", "agency_name": "Two", "agency_noc": "TWO"},
    ])
    connection = sqlite3.connect(":memory:")
    weca.create_tables(connection)

    loaded = weca.load_csv_filtered(
        connection, "agency", path, set(), "agency_id", required=False)

    assert loaded == 0
    assert connection.execute("SELECT COUNT(*) FROM agency").fetchone()[0] == 0
    connection.close()


def test_stop_time_loader_returns_only_referenced_stops(tmp_path):
    path = tmp_path / "stop_times.txt"
    fields = [
        "trip_id", "arrival_time", "departure_time", "stop_id",
        "stop_sequence", "stop_headsign", "pickup_type", "drop_off_type",
        "shape_dist_traveled", "timepoint",
    ]
    write_csv(path, fields, [
        {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "1"},
        {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "2"},
        {"trip_id": "T2", "stop_id": "S3", "stop_sequence": "1"},
    ])
    connection = sqlite3.connect(":memory:")
    weca.create_tables(connection)

    loaded, stops = weca.load_stop_times_filtered(connection, path, {"T1"})

    assert loaded == 2
    assert stops == {"S1", "S2"}
    assert connection.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0] == 2
    connection.close()


def test_shape_import_publishes_only_derived_route_shapes(
        tmp_path, monkeypatch):
    database = tmp_path / "candidate.db"
    shapes = tmp_path / "shapes.txt"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE agency (agency_id TEXT PRIMARY KEY, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, shape_id TEXT,
            direction_id INTEGER);
        INSERT INTO agency VALUES ('A', 'FBRI');
        INSERT INTO routes VALUES ('R1', 'A', '1');
        INSERT INTO trips VALUES ('T1', 'R1', 'SH1', 0);
    """)
    connection.commit()
    connection.close()
    write_csv(shapes, [
        "shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence",
    ], [
        {"shape_id": "SH1", "shape_pt_lat": "51.45",
         "shape_pt_lon": "-2.59", "shape_pt_sequence": "1"},
        {"shape_id": "SH1", "shape_pt_lat": "51.46",
         "shape_pt_lon": "-2.58", "shape_pt_sequence": "2"},
        {"shape_id": "OTHER", "shape_pt_lat": "50.0",
         "shape_pt_lon": "-1.0", "shape_pt_sequence": "1"},
    ])
    monkeypatch.setattr(import_shapes, "DB_PATH", str(database))
    monkeypatch.setattr(import_shapes, "SHAPES_PATH", shapes)
    monkeypatch.setenv("BBB_CANDIDATE_BUILD", "1")

    import_shapes.main()

    check = sqlite3.connect(database)
    assert check.execute("SELECT COUNT(*) FROM route_shapes").fetchone()[0] == 1
    assert check.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shapes'"
    ).fetchone()[0] == 0
    check.close()
