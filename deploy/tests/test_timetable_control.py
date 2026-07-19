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
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (route_short_name TEXT, agency_id TEXT);
        CREATE TABLE calendar (end_date TEXT);
        CREATE TABLE calendar_dates (date TEXT, exception_type INTEGER);
        CREATE TABLE route_shapes (route_id TEXT);
        INSERT INTO agency VALUES ('first', 'FBRI');
    """)
    connection.executemany(
        "INSERT INTO routes VALUES (?, 'first')", [(route,) for route in routes])
    connection.execute("INSERT INTO calendar VALUES (?)", (latest,))
    connection.executemany(
        "INSERT INTO route_shapes VALUES (?)", [(str(index),) for index in range(shapes)])
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
