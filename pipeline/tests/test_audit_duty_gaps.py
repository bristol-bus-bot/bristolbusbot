from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402


DAY = "20260822"
OPERATOR = "FBRI"


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE expected_trips (
               service_date TEXT NOT NULL,
               operator TEXT NOT NULL,
               route TEXT,
               trip_id TEXT NOT NULL,
               direction INTEGER,
               first_departure TEXT,
               block_id TEXT,
               last_departure TEXT
           );
           CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL,
               operator TEXT NOT NULL,
               trip_id TEXT NOT NULL,
               vehicle_ref TEXT,
               siri_journey_ref TEXT,
               match_tier TEXT
           );
           CREATE TABLE poll_log (
               poll_at TEXT PRIMARY KEY, ok INTEGER, vehicles_total INTEGER,
               candidates INTEGER, matched INTEGER, obs_written INTEGER,
               dropped_insane INTEGER, stale INTEGER
           );"""
    )
    audit_rollup.init_summary_tables(connection)
    connection.execute(
        """INSERT INTO daily_trip_coverage_days VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DAY, 1, "[]", None, None, 100, 100, 100, 100.0, 100.0, 30,
         1000, 950, 95.0, 0, 0, 0, 0, 0, 0, 0),
    )
    return connection


def add_trip(connection, block, trip, route, start, end, *, direction=0):
    connection.execute(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?)",
        (DAY, OPERATOR, route, trip, direction, start, block, end),
    )


def add_observation(connection, trip, vehicle, ref, tier="fuzzy"):
    connection.execute(
        """INSERT INTO timepoint_observations
               (service_date, operator, trip_id, vehicle_ref,
                siri_journey_ref, match_tier)
           VALUES (?,?,?,?,?,?)""",
        (DAY, OPERATOR, trip, vehicle, ref, tier),
    )


def add_three_trip_block(
        connection, block, route, starts, ends, vehicles,
        *, refs=None, direction=0):
    trip_ids = [f"{block}-{position}" for position in ("previous", "gap", "next")]
    for trip, start, end in zip(trip_ids, starts, ends):
        add_trip(
            connection, block, trip, route, start, end,
            direction=direction)
    references = refs or [
        start.replace(":", "")[:4] for start in starts]
    if vehicles[0]:
        add_observation(
            connection, trip_ids[0], vehicles[0], references[0])
    if vehicles[1]:
        add_observation(
            connection, trip_ids[1], vehicles[1], references[1])
    if vehicles[2]:
        add_observation(
            connection, trip_ids[2], vehicles[2], references[2])
    return trip_ids


def populated_database() -> sqlite3.Connection:
    connection = database()
    add_three_trip_block(
        connection, "GOOD", "G",
        ["08:00:00", "08:40:00", "09:10:00"],
        ["08:30:00", "09:00:00", "09:40:00"],
        ["FBRI-1", None, "FBRI-1"],
    )
    add_three_trip_block(
        connection, "AMBIGUOUS", "A",
        ["10:00:00", "10:15:00", "10:30:00"],
        ["10:10:00", "10:20:00", "10:40:00"],
        ["FBRI-2", None, "FBRI-2"],
    )
    # A second active journey in the previous trip's fuzzy time window makes
    # the otherwise tempting AMBIGUOUS gap unsafe.
    add_trip(
        connection, "SINGLE", "ambiguous-alternative", "A",
        "10:05:00", "10:12:00")
    add_three_trip_block(
        connection, "LONG", "L",
        ["12:00:00", "14:00:00", "16:00:00"],
        ["12:10:00", "14:10:00", "16:10:00"],
        ["FBRI-3", None, "FBRI-3"],
    )
    add_three_trip_block(
        connection, "SWAP", "S",
        ["17:00:00", "17:30:00", "18:00:00"],
        ["17:20:00", "17:50:00", "18:20:00"],
        ["FBRI-4", None, "FBRI-5"],
    )
    multi = add_three_trip_block(
        connection, "MULTI", "M",
        ["19:00:00", "19:30:00", "20:00:00"],
        ["19:20:00", "19:50:00", "20:20:00"],
        ["FBRI-6", None, "FBRI-6"],
    )
    add_observation(connection, multi[0], "FBRI-7", "1900")
    add_three_trip_block(
        connection, "REFMISMATCH", "R",
        ["21:00:00", "21:30:00", "22:00:00"],
        ["21:20:00", "21:50:00", "22:20:00"],
        ["FBRI-8", None, "FBRI-8"],
        refs=["2059", "2130", "2200"],
    )
    connection.commit()
    return connection


def test_strict_rollup_rejects_ambiguous_long_and_swapped_gaps():
    connection = populated_database()

    result = audit_rollup.rollup_duty_gaps(connection, DAY, [OPERATOR])

    assert result[OPERATOR] == {
        "valid": True,
        "invalid_reasons": [],
        "scheduled": 19,
        "detail": 19,
        "detail_pct": 100.0,
        "blocks": 7,
        "usable_blocks": 7,
        "missing_middle": 6,
        "same_vehicle": 4,
        "short_connections": 3,
        "ambiguous": 2,
        "candidates": 1,
    }
    assert connection.execute(
        """SELECT trip_id, route, vehicle_ref, connection_before_s,
                  connection_after_s, previous_match_window,
                  next_match_window
             FROM valid_daily_duty_gap_candidates"""
    ).fetchall() == [
        ("GOOD-gap", "G", "FBRI-1", 600, 600, 1, 1),
    ]

    # A rerun replaces the same receipt and day result.
    assert audit_rollup.rollup_duty_gaps(
        connection, DAY, [OPERATOR]) == result
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_duty_gap_candidates").fetchone()[0] == 1


def test_invalid_health_day_cannot_retain_candidates():
    connection = populated_database()
    audit_rollup.rollup_duty_gaps(connection, DAY, [OPERATOR])
    connection.execute(
        "UPDATE daily_trip_coverage_days SET is_valid=0 WHERE service_date=?",
        (DAY,),
    )

    result = audit_rollup.rollup_duty_gaps(connection, DAY, [OPERATOR])

    assert result[OPERATOR]["valid"] is False
    assert result[OPERATOR]["invalid_reasons"] == [
        "trip_coverage_day_invalid"]
    assert result[OPERATOR]["candidates"] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_duty_gap_candidates").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM valid_daily_duty_gap_candidates").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM valid_daily_duty_gap_days").fetchone()[0] == 0


def test_old_snapshot_without_end_times_is_explicitly_invalid():
    connection = populated_database()
    connection.execute(
        "UPDATE expected_trips SET last_departure=NULL WHERE trip_id='GOOD-gap'")

    result = audit_rollup.rollup_duty_gaps(connection, DAY, [OPERATOR])

    assert result[OPERATOR]["valid"] is False
    assert result[OPERATOR]["invalid_reasons"] == [
        "duty_detail_coverage_below_95pct",
        "unusable_timetable_blocks",
    ]
    assert result[OPERATOR]["candidates"] == 0


def test_raw_pruning_keeps_private_duty_gap_history():
    connection = populated_database()
    audit_rollup.rollup_duty_gaps(connection, DAY, [OPERATOR])
    connection.execute(
        "UPDATE timepoint_observations SET service_date='20260101'")
    connection.execute(
        "INSERT INTO poll_log VALUES ('2026-01-01T08:00:00+00:00',1,1,1,1,1,0,0)")
    connection.commit()

    assert audit_rollup.prune_old_raw(connection, "20260201") > 0
    assert connection.execute(
        "SELECT COUNT(*) FROM timepoint_observations").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_duty_gap_days").fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM daily_duty_gap_candidates").fetchone()[0] == 1
