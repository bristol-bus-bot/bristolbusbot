"""#131: early arrival at a trip's last timing point counts as on time."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402
import on_time_rule  # noqa: E402
import sample_quality  # noqa: E402


def test_the_rule_in_one_place():
    band = on_time_rule.delay_band
    assert band(-200, is_final=True) == "on_time"      # early at the last stop
    assert band(-200) == "early"                         # early elsewhere
    assert band(400, is_final=True) == "late"            # late is still late
    assert band(-60) == band(359) == "on_time"
    assert on_time_rule.delay_bucket(-400, is_final=True) == "on_time"
    assert on_time_rule.delay_bucket(-400) == "early_5plus"
    assert on_time_rule.delay_bucket(1300, is_final=True) == "late_20plus"
    assert on_time_rule.final_early(-61, 1) and not on_time_rule.final_early(-60, 1)
    assert not on_time_rule.final_early(-500, 0)


def test_sql_rule_matches_the_python_rule():
    connection = sqlite3.connect(":memory:")
    cases = [(delay, final) for delay in (-901, -301, -61, -60, 0, 359, 360, 1201)
             for final in (0, 1, None)]
    for delay, final in cases:
        on_time, early = connection.execute(
            f"SELECT {on_time_rule.on_time_sql('d', 'f')}, {on_time_rule.early_sql('d', 'f')} "
            "FROM (SELECT ? AS d, ? AS f)", (delay, final)).fetchone()
        assert bool(on_time) == on_time_rule.is_on_time(delay, final), (delay, final)
        assert bool(early) == (on_time_rule.delay_band(delay, final) == "early"), (delay, final)


def make_day(connection, *, with_final_column=True):
    final = ", is_final INTEGER NOT NULL DEFAULT 0" if with_final_column else ""
    connection.executescript(f"""
        CREATE TABLE timepoint_observations (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            siri_journey_ref TEXT, stop_sequence INTEGER, stop_code TEXT,
            scheduled_local TEXT, observed_delay_s INTEGER, on_time INTEGER,
            gps_distance_m INTEGER, recorded_at TEXT, vehicle_ref TEXT,
            is_origin INTEGER NOT NULL DEFAULT 0{final}
        );
        CREATE TABLE expected_trips (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            first_departure TEXT
        );
    """)
    readings = [
        # trip, sequence, delay, final
        ("t1", 2, 30, 0),       # on time
        ("t1", 5, -200, 0),     # early at an intermediate stop: still early
        ("t1", 9, -240, 1),     # early at the last stop: on time
        ("t2", 3, 500, 0),      # late
        ("t2", 9, 420, 1),      # late at the last stop: still late
    ]
    for index, (trip, sequence, delay, is_final) in enumerate(readings):
        values = ("20261020", "FBRI", "75", trip, "J" + trip, sequence, f"S{sequence}",
                  f"2026-10-20T10:{index:02d}:00+01:00", delay, int(-60 <= delay <= 359), 20,
                  f"2026-10-20T09:{index:02d}:30+00:00", "FBRI-1", 0)
        if with_final_column:
            values = (*values, is_final)
        connection.execute(
            f"INSERT INTO timepoint_observations VALUES ({','.join('?' * len(values))})", values)
    audit_rollup.init_summary_tables(connection)


def test_rollup_counts_final_stop_early_as_on_time_and_keeps_the_strict_count():
    connection = sqlite3.connect(":memory:")
    make_day(connection)
    result = audit_rollup.rollup(connection, "20261020", ["FBRI"], "FBRI")

    assert result["in_gate"] == 5
    assert result["on_time_pct"] == 40.0          # 2 of 5; strictly 1 of 5
    assert result["final_readings"] == 2 and result["final_early_on_time"] == 1
    on_time, early, late = connection.execute(
        "SELECT on_time, early, late FROM daily_overall_summary WHERE operator='FBRI'").fetchone()
    assert (on_time, early, late) == (2, 1, 2)
    assert on_time + early + late == 5             # totals still add up
    hist = dict(connection.execute(
        "SELECT bucket, n FROM daily_delay_histogram WHERE route IS NULL"))
    assert hist == {"on_time": 2, "early_1_5": 1, "late_6_10": 2}
    assert sum(hist.values()) == 5
    assert connection.execute(
        "SELECT final_readings, final_early_on_time FROM daily_final_stop_summary "
        "WHERE route IS NULL").fetchone() == (2, 1)


def test_hours_geography_and_sample_support_use_the_same_rule():
    connection = sqlite3.connect(":memory:")
    make_day(connection)
    audit_rollup.rollup_hours(connection, "20261020", ["FBRI"], "FBRI")
    assert connection.execute(
        "SELECT SUM(on_time), SUM(early), SUM(late) FROM daily_hour_summary "
        "WHERE route IS NULL").fetchone() == (2, 1, 2)

    geo_index = {f"S{n}": {"area": "Bristol", "ward": "Central"} for n in range(10)}
    buckets, _routes = audit_rollup._geo_route_buckets(
        connection, "20261020", ["FBRI"], geo_index)
    assert buckets[("area", "Bristol")]["on_time"] == 2

    sample_quality.write_day(connection, "20261020", geo_index)
    support = sample_quality.read_support(connection, ["20261020"], "FBRI")
    assert support["readings"] == 5 and support["on_time"] == 2


def test_older_tables_without_the_flag_keep_the_strict_rule():
    connection = sqlite3.connect(":memory:")
    make_day(connection, with_final_column=False)
    result = audit_rollup.rollup(connection, "20261020", ["FBRI"], "FBRI")
    assert result["on_time_pct"] == 20.0 and result["final_early_on_time"] == 0


def test_restatement_reports_before_and_after(monkeypatch):
    connection = sqlite3.connect(":memory:")
    make_day(connection)
    connection.execute(
        "INSERT INTO daily_overall_summary (service_date, operator, readings_in_gate, on_time, "
        "expected_trips) VALUES ('20261020', 'ALL', 5, 1, 2)")
    connection.execute(
        "INSERT INTO daily_overall_summary (service_date, operator, readings_in_gate, on_time) "
        "VALUES ('20260801', 'ALL', 9, 3)")         # before the rule date: not restated
    assert audit_rollup.final_stop_restatement_dates(connection) == ["20261020"]

    calls = []

    def fake_public_day(conn, day, coverage_valid, *_args, **_kwargs):
        calls.append((day, coverage_valid))
        conn.execute("UPDATE daily_overall_summary SET on_time = 2 WHERE service_date = ?", (day,))
        conn.execute("INSERT INTO daily_final_stop_summary VALUES (?, 'ALL', NULL, 2, 1)", (day,))

    monkeypatch.setattr(audit_rollup, "rollup_public_day", fake_public_day)
    [row] = audit_rollup.restate_final_stop(connection, ["20261020"], {}, {}, report=lambda _: None)
    assert calls == [("20261020", True)]          # coverage validity kept
    assert row == {"service_date": "20261020", "readings_before": 5, "on_time_before": 1,
                   "readings_after": 5, "on_time_after": 2, "final_early_on_time": 1}
