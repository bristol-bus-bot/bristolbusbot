from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


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


def test_large_historical_evidence_loss_is_refused():
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
    audit_rollup.init_summary_tables(connection)
    connection.execute(
        "INSERT INTO daily_overall_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("20260701", "FBRI", 80, 60, 10, 10, 75.0, 100, 90,
         100, 20, 10, 20, 15, 75.0),
    )
    connection.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("20260701", "FBRI", "75", f"trip-{index}", 2, "STOP",
             "2026-07-01T10:10:00+01:00", 0, 1, 10,
             "2026-07-01T09:10:00+00:00", "FBRI-1", 0, "exact")
            for index in range(10)
        ],
    )
    connection.commit()

    with pytest.raises(RuntimeError, match=(
            "retained raw evidence would reduce readings_total from 100 to 10")):
        audit_rollup.rollup(
            connection, "20260701", ["FBRI"], "FBRI")

    retained = connection.execute(
        "SELECT readings_total FROM daily_overall_summary "
        "WHERE service_date='20260701' AND operator='FBRI'"
    ).fetchone()[0]
    assert retained == 100


def test_public_day_rollup_rolls_back_every_product_on_late_failure(monkeypatch):
    connection = sqlite3.connect(":memory:")
    audit_rollup.init_summary_tables(connection)

    def fake_rollup(conn, day, _operators, label, **_kwargs):
        conn.execute(
            "INSERT INTO daily_overall_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, label, 1, 1, 0, 0, 100.0, 0, 0, 1, 0, 0,
             None, None, None),
        )
        return {"readings_total": 1}

    monkeypatch.setattr(audit_rollup, "rollup", fake_rollup)
    monkeypatch.setattr(
        audit_rollup, "rollup_geo", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        audit_rollup, "rollup_fleet", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        audit_rollup, "rollup_frequency", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        audit_rollup, "day_consistency_reasons",
        lambda *_args, **_kwargs: ["deliberate_test_contradiction"])

    with pytest.raises(RuntimeError, match="deliberate_test_contradiction"):
        audit_rollup.rollup_public_day(
            connection, "20260820", True, {}, {})

    assert connection.execute(
        "SELECT COUNT(*) FROM daily_overall_summary"
    ).fetchone()[0] == 0
