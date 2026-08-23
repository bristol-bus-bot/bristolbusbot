from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_export  # noqa: E402


DAY = "20260820"


def database(*, health_table: bool = True,
             valid: bool | None = True) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE daily_overall_summary (
               service_date TEXT, operator TEXT,
               on_time INTEGER, early INTEGER, late INTEGER,
               on_time_pct REAL, mean_delay_s INTEGER, median_delay_s INTEGER,
               readings_in_gate INTEGER, readings_total INTEGER,
               excluded_distance INTEGER, median_gate_dist_m INTEGER,
               expected_trips INTEGER, observed_trips INTEGER,
               coverage_pct REAL
           );
           CREATE TABLE daily_route_summary (
               service_date TEXT, operator TEXT, route TEXT,
               on_time_pct REAL, mean_delay_s INTEGER, median_delay_s INTEGER,
               readings_in_gate INTEGER, on_time INTEGER, early INTEGER,
               late INTEGER, expected_trips INTEGER, observed_trips INTEGER,
               coverage_pct REAL
           );"""
    )
    connection.execute(
        "INSERT INTO daily_overall_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DAY, "FBRI", 80, 5, 15, 80.0, 120, 60, 100, 110, 10, 20,
         200, 150, 75.0),
    )
    connection.execute(
        "INSERT INTO daily_route_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DAY, "FBRI", "75", 80.0, 120, 60, 100, 80, 5, 15,
         200, 150, 75.0),
    )
    if health_table:
        connection.execute(
            """CREATE TABLE daily_trip_coverage_days (
                   service_date TEXT PRIMARY KEY, is_valid INTEGER NOT NULL
               )"""
        )
        if valid is not None:
            connection.execute(
                "INSERT INTO daily_trip_coverage_days VALUES (?,?)",
                (DAY, int(valid)),
            )
    connection.commit()
    return connection


def coverage_values(day):
    operator = day["by_operator"]["FBRI"]
    return (
        tuple(operator["overall"][key] for key in audit_export.COVERAGE_COLS),
        tuple(operator["routes"][0][key] for key in audit_export.COVERAGE_COLS),
        tuple(day["overall"][key] for key in audit_export.COVERAGE_COLS),
        tuple(day["routes"][0][key] for key in audit_export.COVERAGE_COLS),
    )


def test_invalid_day_withholds_all_public_coverage_fields():
    connection = database(valid=False)

    day = audit_export.build_day(connection.cursor(), DAY)

    assert coverage_values(day) == ((None, None, None),) * 4


def test_valid_day_keeps_public_coverage_fields():
    connection = database(valid=True)

    day = audit_export.build_day(connection.cursor(), DAY)

    assert coverage_values(day) == ((200, 150, 75.0),) * 4


def test_migrated_day_without_health_row_fails_closed():
    connection = database(valid=None)

    day = audit_export.build_day(connection.cursor(), DAY)

    assert coverage_values(day) == ((None, None, None),) * 4


def test_legacy_database_without_health_table_remains_exportable():
    connection = database(health_table=False)

    day = audit_export.build_day(connection.cursor(), DAY)

    assert coverage_values(day) == ((200, 150, 75.0),) * 4


def test_broken_health_table_stops_export_instead_of_leaking_coverage():
    connection = database(health_table=False)
    connection.execute(
        "CREATE TABLE daily_trip_coverage_days (service_date TEXT PRIMARY KEY)"
    )

    with pytest.raises(sqlite3.OperationalError, match="is_valid"):
        audit_export.build_day(connection.cursor(), DAY)
