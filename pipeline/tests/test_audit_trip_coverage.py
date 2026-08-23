from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402


DAY = "20260820"
OPERATORS = ["FBRI"]


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, stop_sequence INTEGER NOT NULL,
               stop_code TEXT, scheduled_local TEXT, observed_delay_s INTEGER,
               on_time INTEGER, gps_distance_m INTEGER, recorded_at TEXT,
               vehicle_ref TEXT, is_origin INTEGER NOT NULL DEFAULT 0,
               match_tier TEXT
           );
           CREATE TABLE expected_trips (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, direction INTEGER,
               first_departure TEXT
           );
           CREATE TABLE poll_log (
               poll_at TEXT PRIMARY KEY, ok INTEGER, vehicles_total INTEGER,
               candidates INTEGER, matched INTEGER, obs_written INTEGER,
               dropped_insane INTEGER, stale INTEGER
           );"""
    )
    audit_rollup.init_summary_tables(connection)
    return connection


def add_expected(connection: sqlite3.Connection, trip: str, departure: str,
                 *, route: str = "75", direction: int = 0) -> None:
    connection.execute(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?)",
        (DAY, "FBRI", route, trip, direction, departure),
    )


def add_observed(connection: sqlite3.Connection, trip: str, departure: str,
                 tier: str | None, *, route: str = "75",
                 sequence: int = 1) -> None:
    connection.execute(
        """INSERT INTO timepoint_observations VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DAY, "FBRI", route, trip, sequence, f"STOP-{sequence}",
         f"2026-08-20T{departure}+01:00", 0, 1, 10,
         f"2026-08-20T{departure}+01:00", "FBRI-1", 0, tier),
    )


def add_healthy_polls(connection: sqlite3.Connection, rows: list[dict],
                      monkeypatch) -> None:
    monkeypatch.setattr(audit_rollup, "COLLECTOR_POLL_INTERVAL_S", 15 * 60)
    monkeypatch.setattr(audit_rollup, "MAX_POLL_BOUNDARY_GAP_S", 15 * 60)
    monkeypatch.setattr(audit_rollup, "MAX_SUCCESSFUL_POLL_GAP_S", 15 * 60)
    start, end, invalid = audit_rollup.scheduled_poll_window(DAY, rows)
    assert invalid == 0
    current = start
    while current < end:
        connection.execute(
            "INSERT INTO poll_log VALUES (?,?,?,?,?,?,?,?)",
            (current.isoformat(), 1, 12, 10, 9, 4, 0, 0),
        )
        current += timedelta(minutes=15)
    connection.commit()


def test_healthy_day_is_durable_reconciled_and_idempotent(monkeypatch):
    connection = database()
    planned = [
        ("am", "07:00:00", 0),
        ("day", "11:00:00", 1),
        ("pm", "16:00:00", 0),
        ("evening", "20:00:00", 1),
    ]
    for trip, departure, direction in planned:
        add_expected(connection, trip, departure, direction=direction)
    add_observed(connection, "am", "07:05:00", "exact")
    add_observed(connection, "day", "11:05:00", "fuzzy")
    add_observed(connection, "pm", "16:05:00", None)
    # An unscheduled observation is not allowed to inflate either coverage
    # answer.
    add_observed(connection, "not-in-snapshot", "12:05:00", "exact")
    rows = audit_rollup.load_trip_coverage_rows(connection, DAY, OPERATORS)
    add_healthy_polls(connection, rows, monkeypatch)

    result = audit_rollup.rollup_trip_coverage(connection, DAY, OPERATORS)

    assert result["valid"] is True
    assert (result["scheduled"], result["observed"], result["unobserved"]) == (
        4, 3, 1)
    assert (result["exact"], result["fuzzy"], result["unknown"]) == (1, 1, 1)
    assert connection.execute(
        "SELECT COUNT(*) FROM valid_daily_trip_coverage"
    ).fetchone()[0] == 8  # four FBRI groups plus four network groups
    assert connection.execute(
        """SELECT time_band, direction, scheduled_trips, observed_trips,
                  unobserved_trips
             FROM daily_trip_coverage
            WHERE operator='FBRI' ORDER BY time_band"""
    ).fetchall() == [
        ("am_peak", 0, 1, 1, 0),
        ("evening", 1, 1, 0, 1),
        ("interpeak", 1, 1, 1, 0),
        ("pm_peak", 0, 1, 1, 0),
    ]

    expected, observed = audit_rollup.route_trip_counts(
        connection, DAY, OPERATORS)
    assert expected == {"75": 4}
    assert observed == {"75": 3}
    public = audit_rollup.rollup(
        connection, DAY, OPERATORS, "FBRI", coverage_valid=True)
    assert (public["expected"], public["observed"]) == (4, 3)

    first_day = connection.execute(
        "SELECT * FROM daily_trip_coverage_days WHERE service_date=?", (DAY,)
    ).fetchone()
    first_groups = connection.execute(
        "SELECT * FROM daily_trip_coverage ORDER BY 1,2,3,4,5"
    ).fetchall()
    audit_rollup.rollup_trip_coverage(connection, DAY, OPERATORS)
    assert connection.execute(
        "SELECT * FROM daily_trip_coverage_days WHERE service_date=?", (DAY,)
    ).fetchone() == first_day
    assert connection.execute(
        "SELECT * FROM daily_trip_coverage ORDER BY 1,2,3,4,5"
    ).fetchall() == first_groups


def test_unhealthy_day_is_visible_but_excluded_from_safe_and_public_coverage():
    connection = database()
    add_expected(connection, "trip-1", "07:00:00")
    add_expected(connection, "trip-2", "07:30:00")
    add_observed(connection, "trip-1", "07:05:00", "fuzzy")
    connection.commit()

    result = audit_rollup.rollup_trip_coverage(connection, DAY, OPERATORS)

    assert result["valid"] is False
    assert "no_successful_polls" in result["invalid_reasons"]
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_trip_coverage"
    ).fetchone()[0] == 2  # operator plus whole-network evidence is retained
    assert connection.execute(
        "SELECT COUNT(*) FROM valid_daily_trip_coverage"
    ).fetchone()[0] == 0

    public = audit_rollup.rollup(
        connection, DAY, OPERATORS, "FBRI", coverage_valid=False)
    assert public["coverage_valid"] is False
    assert (public["expected"], public["observed"], public["coverage_pct"]) == (
        None, None, None)
    assert connection.execute(
        """SELECT expected_trips, observed_trips, coverage_pct
             FROM daily_route_summary
            WHERE service_date=? AND operator='FBRI'""",
        (DAY,),
    ).fetchone() == (None, None, None)


def test_matching_quality_collapse_invalidates_otherwise_complete_day(
        monkeypatch):
    connection = database()
    add_expected(connection, "trip-1", "07:00:00")
    add_expected(connection, "trip-2", "07:30:00")
    add_observed(connection, "trip-1", "07:05:00", "fuzzy")
    rows = audit_rollup.load_trip_coverage_rows(connection, DAY, OPERATORS)
    add_healthy_polls(connection, rows, monkeypatch)
    connection.execute("UPDATE poll_log SET matched = 5")
    connection.commit()

    result = audit_rollup.rollup_trip_coverage(connection, DAY, OPERATORS)

    assert result["successful_poll_coverage_pct"] == 100.0
    assert result["match_rate_pct"] == 50.0
    assert result["valid"] is False
    assert result["invalid_reasons"] == ["matching_rate_below_80pct"]


def test_extended_gtfs_time_keeps_the_window_open_next_morning():
    connection = database()
    add_expected(connection, "trip-1", "01:00:00")
    add_expected(connection, "trip-2", "29:42:00")
    rows = audit_rollup.load_trip_coverage_rows(connection, DAY, OPERATORS)

    start, end, invalid = audit_rollup.scheduled_poll_window(DAY, rows)

    assert invalid == 0
    assert start == datetime(2026, 8, 19, 23, 45, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 21, 5, 42, tzinfo=timezone.utc)


def test_raw_pruning_keeps_permanent_trip_coverage():
    connection = database()
    connection.execute(
        """INSERT INTO daily_trip_coverage_days VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("20260101", 0, '["no_successful_polls"]', None, None,
         0, 0, 0, None, None, None, 0, 0, None, 1, 0, 1, 0, 0, 0, 0),
    )
    connection.execute(
        """INSERT INTO daily_trip_coverage VALUES
               ('20260101','FBRI','75',0,'am_peak',1,0,1,0,0,0)"""
    )
    connection.execute(
        """INSERT INTO timepoint_observations VALUES
               ('20260101','FBRI','75','old-trip',1,'A','2026-01-01T08:00:00',
                0,1,10,'2026-01-01T08:00:00+00:00','FBRI-1',0,'fuzzy')"""
    )
    connection.execute(
        """INSERT INTO poll_log VALUES
               ('2026-01-01T08:00:00+00:00',1,1,1,1,1,0,0)"""
    )
    connection.commit()

    assert audit_rollup.prune_old_raw(connection, "20260201") == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM timepoint_observations"
    ).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_trip_coverage"
    ).fetchone()[0] == 1
