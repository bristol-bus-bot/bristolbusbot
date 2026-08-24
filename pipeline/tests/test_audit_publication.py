from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_publication  # noqa: E402


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE daily_overall_summary (
               service_date TEXT, operator TEXT,
               readings_in_gate INTEGER, readings_total INTEGER,
               on_time INTEGER, early INTEGER, late INTEGER,
               excluded_distance INTEGER
           );"""
    )
    return connection


def add_row(connection, day, operator, values):
    connection.execute(
        "INSERT INTO daily_overall_summary VALUES (?,?,?,?,?,?,?,?)",
        (day, operator, *values),
    )


def test_consistent_pooled_and_operator_totals_are_publishable():
    connection = database()
    add_row(connection, "20260820", "FBRI", (80, 100, 60, 10, 10, 20))
    add_row(connection, "20260820", "SCGL", (8, 10, 6, 1, 1, 2))
    add_row(connection, "20260820", "ALL", (88, 110, 66, 11, 11, 22))

    assert audit_publication.day_consistency_reasons(
        connection, "20260820") == []


def test_pooled_total_contradiction_fails_closed():
    connection = database()
    add_row(connection, "20260820", "FBRI", (80, 100, 60, 10, 10, 20))
    add_row(connection, "20260820", "ALL", (79, 99, 60, 10, 9, 20))

    reasons = audit_publication.day_consistency_reasons(
        connection, "20260820")

    assert "pooled_readings_in_gate_does_not_equal_operator_sum" in reasons
    assert "pooled_readings_total_does_not_equal_operator_sum" in reasons
    assert "pooled_late_does_not_equal_operator_sum" in reasons


def test_known_cutover_day_is_excluded_even_when_totals_look_consistent():
    connection = database()
    add_row(connection, "20260701", "FBRI", (80, 100, 60, 10, 10, 20))
    add_row(connection, "20260701", "ALL", (80, 100, 60, 10, 10, 20))

    exclusions = audit_publication.publication_exclusions(
        connection, ["20260701"])

    assert exclusions == {
        "20260701": ["partial_raw_history_after_collector_cutover"]}


def test_legacy_pooled_only_day_is_not_invented_as_a_contradiction():
    connection = database()
    add_row(connection, "20260820", "ALL", (80, 100, 60, 10, 10, 20))

    assert audit_publication.day_consistency_reasons(
        connection, "20260820") == []
