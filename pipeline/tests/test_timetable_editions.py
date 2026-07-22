import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from timetable_editions import normalize_database, validate_database


def database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE routes (route_id TEXT PRIMARY KEY);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT);
        CREATE TABLE calendar (
            service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        CREATE INDEX idx_trips_service ON trips(service_id);
        INSERT INTO routes VALUES ('R1');
    """)
    return connection


def calendar(connection, service_id, start, end, days=(1, 1, 1, 1, 1, 1, 1)):
    connection.execute(
        "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)",
        (service_id, *days, start, end))


def trip(connection, trip_id, route_id, service_id):
    connection.execute(
        "INSERT INTO trips VALUES (?,?,?)", (trip_id, route_id, service_id))


def test_replacement_editions_receive_non_overlapping_windows(tmp_path):
    path = tmp_path / "timetable.db"
    connection = database(path)
    calendar(connection, "CURRENT", "20260722", "20270422")
    calendar(connection, "FUTURE", "20260726", "20270426")
    trip(connection, "T_CURRENT", "R1", "CURRENT")
    trip(connection, "T_FUTURE", "R1", "FUTURE")
    connection.executemany(
        "INSERT INTO calendar_dates VALUES (?,?,?)", [
            ("CURRENT", "20260723", 2),
            ("CURRENT", "20260831", 1),
        ])
    connection.commit()
    connection.close()

    result = normalize_database(path)
    assert result == {
        "route_editions": 2,
        "superseded_route_editions": 1,
        "trips_rewindowed": 1,
    }

    check = sqlite3.connect(path)
    current_service = check.execute(
        "SELECT service_id FROM trips WHERE trip_id='T_CURRENT'").fetchone()[0]
    assert current_service.startswith("BBBWIN_")
    assert check.execute(
        "SELECT start_date,end_date FROM calendar WHERE service_id=?",
        (current_service,)).fetchone() == ("20260722", "20260725")
    assert check.execute(
        "SELECT date,exception_type FROM calendar_dates WHERE service_id=?",
        (current_service,)).fetchall() == [("20260723", 2)]
    assert check.execute(
        "SELECT effective_end,superseded_by FROM route_service_editions "
        "WHERE route_id='R1' AND edition_start='20260722'").fetchone() == (
            "20260725", "20260726")
    assert validate_database(check, require_table=True) == {
        "route_editions": 2,
        "superseded_route_editions": 1,
    }
    check.close()
    assert normalize_database(path)["superseded_route_editions"] == 1


def test_small_day_specific_cohort_is_retained_in_parallel(tmp_path):
    path = tmp_path / "timetable.db"
    connection = database(path)
    calendar(connection, "BASE", "20260722", "20270422")
    calendar(
        connection, "FRIDAY", "20260830", "20270530",
        days=(0, 0, 0, 0, 1, 0, 0))
    for index in range(8):
        trip(connection, f"BASE_{index}", "R1", "BASE")
    trip(connection, "EXTRA", "R1", "FRIDAY")
    connection.commit()
    connection.close()

    result = normalize_database(path)
    assert result["superseded_route_editions"] == 0
    assert result["trips_rewindowed"] == 0
    check = sqlite3.connect(path)
    assert check.execute(
        "SELECT end_date FROM calendar WHERE service_id='BASE'").fetchone()[0] \
        == "20270422"
    check.close()


def test_shared_calendar_is_cloned_only_for_superseded_route(tmp_path):
    path = tmp_path / "timetable.db"
    connection = database(path)
    connection.execute("INSERT INTO routes VALUES ('R2')")
    calendar(connection, "SHARED", "20260722", "20270422")
    calendar(connection, "R1_NEW", "20260726", "20270426")
    trip(connection, "R1_OLD", "R1", "SHARED")
    trip(connection, "R1_NEW", "R1", "R1_NEW")
    trip(connection, "R2_ONLY", "R2", "SHARED")
    connection.commit()
    connection.close()

    normalize_database(path)
    check = sqlite3.connect(path)
    assert check.execute(
        "SELECT service_id FROM trips WHERE trip_id='R2_ONLY'").fetchone()[0] \
        == "SHARED"
    assert check.execute(
        "SELECT end_date FROM calendar WHERE service_id='SHARED'").fetchone()[0] \
        == "20270422"
    assert check.execute(
        "SELECT service_id FROM trips WHERE trip_id='R1_OLD'").fetchone()[0] \
        != "SHARED"
    check.close()


def test_validator_rejects_tampered_edition_record(tmp_path):
    path = tmp_path / "timetable.db"
    connection = database(path)
    calendar(connection, "ONLY", "20260722", "20270422")
    trip(connection, "T", "R1", "ONLY")
    connection.commit()
    connection.close()
    normalize_database(path)

    check = sqlite3.connect(path)
    check.execute(
        "UPDATE route_service_editions SET trip_count=99 WHERE route_id='R1'")
    check.commit()
    with pytest.raises(RuntimeError, match="differs from timetable"):
        validate_database(check, require_table=True)
    check.close()
