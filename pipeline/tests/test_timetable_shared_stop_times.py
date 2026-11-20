import sqlite3
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import build_timetable  # noqa: E402
import timetable_shared_stop_times as shared  # noqa: E402

COLUMNS = ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
           "pickup_type", "drop_off_type", "timepoint")


def make(path: Path) -> list[tuple]:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT);
        CREATE TABLE stop_times (
            trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT,
            stop_sequence INTEGER, pickup_type INTEGER, drop_off_type INTEGER,
            timepoint INTEGER);
        CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        CREATE INDEX idx_stop_times_trip_seq ON stop_times(trip_id, stop_sequence);
    """)
    # Three editions of the same journey, one different journey, one with a
    # NULL column and one trip with no stop times at all.
    rows = []
    for trip in ("E1-0800", "E2-0800", "E3-0800"):
        rows += [(trip, "08:00:00", "08:00:00", "A", 1, 0, 1, 1),
                 (trip, "08:05:00", "08:05:00", "B", 2, 0, 0, 0),
                 (trip, "08:10:00", "08:10:00", "C", 3, 1, 0, 1)]
    rows += [("E1-0900", "09:00:00", "09:00:00", "A", 1, 0, 1, 1),
             ("E1-0900", "09:06:00", "09:06:00", "B", 2, 0, 0, 0)]
    rows += [("E1-1000", "10:00:00", "10:00:00", "A", 1, None, 1, 1),
             ("E1-1000", "10:05:00", "10:05:00", "B", 2, 0, 0, 0)]
    trips = sorted({row[0] for row in rows}) + ["E1-empty"]
    connection.executemany("INSERT INTO trips VALUES (?, 'R', 'S')", [(t,) for t in trips])
    connection.executemany(f"INSERT INTO stop_times VALUES ({', '.join('?' * 8)})", rows)
    connection.commit()
    connection.close()
    return sorted(rows, key=lambda row: (row[0], row[4]))


def read(path: Path) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM stop_times ORDER BY trip_id, stop_sequence").fetchall()
    finally:
        connection.close()


def test_convert_keeps_identical_rows_and_stores_each_journey_once(tmp_path):
    path = tmp_path / "timetable.db"
    original = make(path)
    counts = shared.convert(path)
    assert read(path) == original
    assert counts == {"stop_times": 13, "stored_rows": 7, "patterns": 3, "trips": 5}

    connection = sqlite3.connect(path)
    assert shared.layout(connection) == shared.SHARED_LAYOUT
    assert connection.execute(
        "SELECT type FROM sqlite_master WHERE name='stop_times'").fetchone()[0] == "view"
    assert [row[1] for row in connection.execute("PRAGMA table_info(stop_times)")] == list(COLUMNS)
    patterns = dict(connection.execute("SELECT trip_id, stop_pattern_id FROM trips"))
    assert patterns["E1-0800"] == patterns["E2-0800"] == patterns["E3-0800"]
    assert patterns["E1-empty"] is None
    # The by-trip lookup every reader uses goes through the pattern index.
    plan = " ".join(str(row) for row in connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM stop_times WHERE trip_id = 'E2-0800' ORDER BY stop_sequence"))
    assert "idx_pattern_stop_times_seq" in plan
    connection.close()


def test_convert_refuses_a_file_already_converted(tmp_path):
    path = tmp_path / "timetable.db"
    make(path)
    shared.convert(path)
    with pytest.raises(RuntimeError, match="already"):
        shared.convert(path)


def test_unconverted_file_reports_repeated_layout(tmp_path):
    path = tmp_path / "timetable.db"
    make(path)
    connection = sqlite3.connect(path)
    assert shared.layout(connection) == "repeated"
    connection.close()


@pytest.mark.parametrize("value, expected", [
    (None, "repeated"), ("", "repeated"), ("repeated", "repeated"),
    ("anything", "repeated"), ("shared", "shared"), (" Shared ", "shared"),
])
def test_build_switch_defaults_to_current_layout(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("BBB_TIMETABLE_LAYOUT", raising=False)
    else:
        monkeypatch.setenv("BBB_TIMETABLE_LAYOUT", value)
    assert build_timetable.timetable_layout_requested() == expected
