from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_export  # noqa: E402
import audit_rollup  # noqa: E402
from audit_operators import NETWORK_LABEL  # noqa: E402


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, stop_sequence INTEGER NOT NULL,
               scheduled_local TEXT, observed_delay_s INTEGER,
               gps_distance_m INTEGER, is_origin INTEGER NOT NULL DEFAULT 0
           )""")
    audit_rollup.init_summary_tables(conn)
    return conn


def add(conn, date, scheduled, delay, *, operator="FBRI", route="43",
        dist=20, origin=0, seq=[0]):
    seq[0] += 1
    conn.execute(
        """INSERT INTO timepoint_observations (service_date, operator, route,
               trip_id, stop_sequence, scheduled_local, observed_delay_s,
               gps_distance_m, is_origin) VALUES (?,?,?,?,?,?,?,?,?)""",
        (date, operator, route, f"T{seq[0]}", 2, scheduled, delay, dist, origin))


def hours(conn, date, label, route):
    clause = "route IS NULL" if route is None else "route = ?"
    args = (date, label) if route is None else (date, label, route)
    return {
        row["hour"]: (row["readings_in_gate"], row["on_time"], row["early"], row["late"])
        for row in conn.execute(
            f"""SELECT * FROM daily_hour_summary
                 WHERE service_date = ? AND operator = ? AND {clause}""", args)
    }


def test_local_hour_reads_the_wall_clock():
    assert audit_rollup.local_hour("2026-10-23 08:15:00+01:00") == 8
    assert audit_rollup.local_hour("2026-10-23T23:59:00+01:00") == 23
    assert audit_rollup.local_hour(None) is None
    assert audit_rollup.local_hour("garbage") is None


def test_hours_use_the_same_gate_and_bands_as_the_daily_figures():
    conn = database()
    day = "20261023"  # a Friday
    add(conn, day, "2026-10-23 08:10:00+01:00", 30)      # on time
    add(conn, day, "2026-10-23 08:40:00+01:00", 420)     # late
    add(conn, day, "2026-10-23 08:50:00+01:00", -90)     # early
    add(conn, day, "2026-10-23 08:55:00+01:00", 30, dist=400)    # outside gate
    add(conn, day, "2026-10-23 08:56:00+01:00", 30, origin=1)    # origin stop
    add(conn, day, "2026-10-23 17:05:00+01:00", 359)     # on time at the edge
    add(conn, day, "2026-10-23 17:06:00+01:00", 10, route=None)  # no route
    audit_rollup.rollup_hours(conn, day, ["FBRI"], "FBRI")
    assert hours(conn, day, "FBRI", "43") == {8: (3, 1, 1, 1), 17: (1, 1, 0, 0)}
    assert hours(conn, day, "FBRI", None) == {8: (3, 1, 1, 1), 17: (2, 2, 0, 0)}
    # Re-running replaces rather than adds.
    audit_rollup.rollup_hours(conn, day, ["FBRI"], "FBRI")
    assert hours(conn, day, "FBRI", "43")[8] == (3, 1, 1, 1)


def test_after_midnight_and_the_repeated_clock_change_hour():
    conn = database()
    saturday = "20261024"
    # Saturday's service runs past midnight into Sunday 25 October, when the
    # clocks go back: 01:30 BST and 01:30 GMT are both the 1am hour.
    add(conn, saturday, "2026-10-24 23:50:00+01:00", 0)
    add(conn, saturday, "2026-10-25 00:30:00+01:00", 0)
    add(conn, saturday, "2026-10-25 01:30:00+01:00", 0)
    add(conn, saturday, "2026-10-25 01:30:00+00:00", 600)
    audit_rollup.rollup_hours(conn, saturday, ["FBRI"], "FBRI")
    assert hours(conn, saturday, "FBRI", "43") == {
        23: (1, 1, 0, 0), 0: (1, 1, 0, 0), 1: (2, 1, 0, 1)}


def test_pooled_network_tags_other_operators_routes():
    conn = database()
    day = "20261023"
    add(conn, day, "2026-10-23 09:00:00+01:00", 0, operator="FBRI", route="13")
    add(conn, day, "2026-10-23 09:00:00+01:00", 0, operator="SCGL", route="13")
    audit_rollup.rollup_hours(conn, day, ["FBRI", "SCGL"], NETWORK_LABEL)
    routes = {row[0] for row in conn.execute(
        "SELECT route FROM daily_hour_summary WHERE route IS NOT NULL")}
    assert "13" in routes and len(routes) == 2
    assert hours(conn, day, NETWORK_LABEL, None) == {9: (2, 2, 0, 0)}


def test_backfill_only_touches_rolled_up_days_with_raw_readings():
    conn = database()
    add(conn, "20261022", "2026-10-22 10:00:00+01:00", 0)
    add(conn, "20261023", "2026-10-23 10:00:00+01:00", 0)
    conn.execute("INSERT INTO daily_overall_summary (service_date, operator) VALUES ('20261023', 'FBRI')")
    conn.execute("INSERT INTO daily_overall_summary (service_date, operator) VALUES ('20260601', 'FBRI')")
    assert audit_rollup.hour_backfill_dates(conn) == ["20261023"]


def test_export_pools_the_window_and_splits_weekends(tmp_path, monkeypatch):
    conn = database()
    dates = [f"202610{d:02d}" for d in range(1, 31)]  # 1-30 October 2026
    for date in dates:
        add(conn, date, f"2026-10-{date[-2:]} 08:15:00+01:00", 30)
        add(conn, date, f"2026-10-{date[-2:]} 08:20:00+01:00", 500)
        audit_rollup.rollup_hours(conn, date, ["FBRI"], "FBRI")
    monkeypatch.setattr(audit_export, "OUT_DIR", str(tmp_path))
    index = audit_export.write_hourly(conn.cursor(), dates, ["FBRI", "SCGL"])
    assert index == {"window_days": 28, "min_readings": 30,
                     "files": {"FBRI": "hourly/FBRI.json"}}
    payload = json.loads((tmp_path / "hourly" / "FBRI.json").read_text())
    # The window is the last 28 published days: 3-30 October.
    assert payload["window"] == {"from": "20261003", "to": "20261030",
                                 "days": {"weekday": 20, "weekend": 8}}
    assert payload["routes"]["43"]["weekday"] == {"8": [20, 0, 20]}
    assert payload["routes"]["43"]["weekend"] == {"8": [8, 0, 8]}
    assert payload["network"]["weekday"] == {"8": [20, 0, 20]}
    assert "7" not in payload["network"]["weekday"]


def test_export_without_hourly_rows_writes_nothing(tmp_path, monkeypatch):
    conn = database()
    monkeypatch.setattr(audit_export, "OUT_DIR", str(tmp_path))
    assert audit_export.write_hourly(conn.cursor(), ["20261023"], ["FBRI"]) is None


def test_journeys_count_only_healthy_days_and_checked_duty_gaps():
    conn = database()
    days = ["20261020", "20261021", "20261022"]
    for day, valid in zip(days, (1, 1, 0)):
        conn.execute(
            """INSERT INTO daily_trip_coverage_days VALUES
                   (?,?,'[]',NULL,NULL,0,0,0,NULL,NULL,NULL,0,0,NULL,0,0,0,0,0,0,0)""",
            (day, valid))
        for band, scheduled, seen in (("am_peak", 10, 9), ("evening", 20, 18)):
            conn.execute(
                "INSERT INTO daily_trip_coverage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (day, "FBRI", "43", 0, band, scheduled, seen, scheduled - seen, seen, 0, 0))
    # Duty-gap check passed on the 20th only.
    for day, valid in (("20261020", 1), ("20261021", 0)):
        conn.execute(
            """INSERT INTO daily_duty_gap_days VALUES (?,?,?,'[]',0,0,NULL,0,0,0,0,0,0,0)""",
            (day, "FBRI", valid))
    for day, trip, departure in (("20261020", "G1", "08:10:00"), ("20261020", "G2", "21:00:00"),
                                 ("20261021", "G3", "08:10:00")):
        conn.execute(
            "INSERT INTO daily_duty_gap_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, "FBRI", "B1", trip, "43", 0, departure, departure, "V1", "P", "43",
             "07:00:00", "0700", "N", "43", "09:00:00", "0900", 60, 60, 1, 1))
    journeys = audit_export.build_journeys(conn.cursor(), days, "FBRI")
    route = journeys["routes"]["43"]
    # Only the two healthy coverage days count; the invalid 22nd is ignored.
    assert route["total"] == [60, 54, 2]
    assert route["bands"]["am_peak"] == [20, 18, 1]
    assert route["bands"]["evening"] == [40, 36, 1]
    assert journeys["network"]["total"] == [60, 54, 2]
    assert journeys["duty_checked"] is True


def test_journeys_absent_without_coverage():
    conn = database()
    assert audit_export.build_journeys(conn.cursor(), ["20261020"], "FBRI") is None
