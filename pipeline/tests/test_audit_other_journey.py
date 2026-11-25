"""Planning #146: "bus was on another journey", an estimate kept separate from
the strict "very likely didn't run" group, which must not change."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402

DAY = "20261118"          # a Wednesday in November: UK time is UTC
OPERATOR = "FBRI"


def database():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE expected_trips (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, direction INTEGER, first_departure TEXT,
               block_id TEXT, last_departure TEXT);
           CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL, operator TEXT NOT NULL,
               trip_id TEXT NOT NULL, vehicle_ref TEXT, siri_journey_ref TEXT,
               match_tier TEXT, recorded_at TEXT);
           CREATE TABLE poll_log (
               poll_at TEXT PRIMARY KEY, ok INTEGER, vehicles_total INTEGER,
               candidates INTEGER, matched INTEGER, obs_written INTEGER,
               dropped_insane INTEGER, stale INTEGER);""")
    audit_rollup.init_summary_tables(connection)
    connection.execute(
        "INSERT INTO daily_trip_coverage_days VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DAY, 1, "[]", None, None, 100, 100, 100, 100.0, 100.0, 30,
         1000, 950, 95.0, 0, 0, 0, 0, 0, 0, 0))
    return connection


def trip(connection, block, trip_id, route, start, end, direction=0, day=DAY):
    connection.execute("INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?)",
                       (day, OPERATOR, route, trip_id, direction, start, block, end))


def seen(connection, trip_id, vehicle, start, at, day=DAY):
    connection.execute(
        "INSERT INTO timepoint_observations (service_date, operator, trip_id, "
        "vehicle_ref, siri_journey_ref, match_tier, recorded_at) VALUES (?,?,?,?,?,?,?)",
        (day, OPERATOR, trip_id, vehicle, start.replace(":", "")[:4], "fuzzy",
         f"{day[:4]}-{day[4:6]}-{day[6:]}T{at}+00:00"))


def moved(connection, day=DAY):
    audit_rollup.rollup_duty_gaps(connection, day, [OPERATOR])
    return {row[0]: row[1:] for row in connection.execute(
        "SELECT trip_id, vehicle_ref, other_trip_id FROM valid_daily_other_journey_gaps")}


def strict(connection):
    return {row[0] for row in connection.execute(
        "SELECT trip_id FROM valid_daily_duty_gap_candidates")}


def block_with_gap(connection, block="B1", route="21", vehicle="FBRI-1"):
    """08:00 seen on the bus, 09:00-09:50 not seen, nothing after."""
    trip(connection, block, f"{block}-before", route, "08:00:00", "08:40:00")
    trip(connection, block, f"{block}-gap", route, "09:00:00", "09:50:00")
    seen(connection, f"{block}-before", vehicle, "08:00:00", "08:20:00")


def test_bus_tracked_on_another_journey_mid_run_moves():
    connection = database()
    block_with_gap(connection)
    trip(connection, "OTHER", "elsewhere", "m1", "09:05:00", "09:45:00")
    seen(connection, "elsewhere", "FBRI-1", "09:05:00", "09:25:00")
    assert moved(connection) == {"B1-gap": ("FBRI-1", "elsewhere")}


def test_reading_too_near_the_start_or_end_does_not_count():
    connection = database()
    block_with_gap(connection)
    trip(connection, "OTHER", "elsewhere", "m1", "09:05:00", "09:45:00")
    seen(connection, "elsewhere", "FBRI-1", "09:05:00", "09:05:00")   # 5 min in
    assert moved(connection) == {}


def test_running_late_on_the_journey_before_does_not_count():
    connection = database()
    block_with_gap(connection)
    seen(connection, "B1-before", "FBRI-1", "08:00:00", "09:20:00")
    connection.execute("DELETE FROM timepoint_observations WHERE recorded_at LIKE '%08:20:00%'")
    assert moved(connection) == {}


def test_another_timetable_copy_of_the_same_journey_does_not_count():
    # The route 8 trap: two copies of one journey in different blocks.
    connection = database()
    block_with_gap(connection, route="8")
    trip(connection, "COPY", "copy", "8", "09:02:00", "09:52:00")
    seen(connection, "copy", "FBRI-1", "09:02:00", "09:25:00")
    assert moved(connection) == {}


def test_different_buses_either_side_claims_nothing():
    connection = database()
    block_with_gap(connection)
    trip(connection, "B1", "B1-after", "21", "10:00:00", "10:40:00")
    seen(connection, "B1-after", "FBRI-2", "10:00:00", "10:20:00")
    trip(connection, "OTHER", "elsewhere", "m1", "09:05:00", "09:45:00")
    seen(connection, "elsewhere", "FBRI-1", "09:05:00", "09:25:00")
    assert moved(connection) == {}


def test_short_journeys_cannot_move():
    connection = database()
    trip(connection, "B1", "before", "21", "08:00:00", "08:40:00")
    trip(connection, "B1", "short", "21", "09:00:00", "09:15:00")
    seen(connection, "before", "FBRI-1", "08:00:00", "08:20:00")
    trip(connection, "OTHER", "elsewhere", "m1", "09:00:00", "09:30:00")
    seen(connection, "elsewhere", "FBRI-1", "09:00:00", "09:08:00")
    assert moved(connection) == {}


def test_strict_group_is_unchanged_and_never_counted_twice():
    # Same bus either side: the strict rule claims it, not the estimate.
    connection = database()
    block_with_gap(connection)
    trip(connection, "B1", "B1-after", "21", "10:00:00", "10:40:00")
    seen(connection, "B1-after", "FBRI-1", "10:00:00", "10:20:00")
    trip(connection, "OTHER", "elsewhere", "m1", "09:05:00", "09:45:00")
    seen(connection, "elsewhere", "FBRI-1", "09:05:00", "09:25:00")
    assert moved(connection) == {}
    assert strict(connection) == {"B1-gap"}


def test_clock_change_day_moves_nothing():
    day = "20261025"
    assert audit_rollup.clock_change_day(day)
    assert not audit_rollup.clock_change_day(DAY)
    connection = database()
    connection.execute("UPDATE daily_trip_coverage_days SET service_date=?", (day,))
    trip(connection, "B1", "before", "21", "08:00:00", "08:40:00", day=day)
    trip(connection, "B1", "gap", "21", "09:00:00", "09:50:00", day=day)
    seen(connection, "before", "FBRI-1", "08:00:00", "08:20:00", day=day)
    trip(connection, "OTHER", "elsewhere", "m1", "09:05:00", "09:45:00", day=day)
    seen(connection, "elsewhere", "FBRI-1", "09:05:00", "09:25:00", day=day)
    assert moved(connection, day) == {}


def test_snapshot_without_reading_times_moves_nothing():
    connection = database()
    connection.execute("ALTER TABLE timepoint_observations RENAME COLUMN recorded_at TO other")
    trip(connection, "B1", "before", "21", "08:00:00", "08:40:00")
    trip(connection, "B1", "gap", "21", "09:00:00", "09:50:00")
    assert audit_rollup.load_vehicle_readings(connection, DAY, [OPERATOR]) == {}
