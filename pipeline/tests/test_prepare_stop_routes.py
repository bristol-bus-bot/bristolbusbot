import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from prepare_stop_routes import build_stop_routes


def make_source_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT,
            stop_lat REAL, stop_lon REAL);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, route_short_name TEXT);
        CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT);
        CREATE TABLE stop_times (
            trip_id TEXT, stop_id TEXT, stop_sequence INTEGER);
        INSERT INTO stops VALUES
            ('S1', '0100A', 'One', 51.45, -2.59),
            ('S2', '0100B', 'Two', 51.46, -2.58);
        INSERT INTO routes VALUES ('R2', '2'), ('R10', '10');
        INSERT INTO trips VALUES
            ('T2', 'R2'), ('T10', 'R10'), ('T10B', 'R10');
        INSERT INTO stop_times VALUES
            ('T2', 'S1', 1),
            ('T10', 'S1', 1),
            ('T10B', 'S1', 1),
            ('T10', 'S2', 2);
    """)
    connection.commit()
    connection.close()


def test_build_stop_routes_materialises_distinct_final_relationships(tmp_path):
    database = tmp_path / "timetable.db"
    make_source_database(database)

    assert build_stop_routes(database) == 3

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT stop_code, route_short_name FROM stop_routes "
        "ORDER BY stop_code, route_short_name").fetchall() == [
            ("0100A", "10"),
            ("0100A", "2"),
            ("0100B", "10"),
        ]
    connection.close()


def test_build_stop_routes_fails_closed_when_a_stop_has_no_route(tmp_path):
    database = tmp_path / "timetable.db"
    make_source_database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO stops VALUES ('ORPHAN', '0100C', 'Three', 51.47, -2.57)")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="missing stop"):
        build_stop_routes(database)
