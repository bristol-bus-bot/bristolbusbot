from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_export  # noqa: E402
import audit_period  # noqa: E402
import audit_rollup  # noqa: E402
from sample_quality import write_day  # noqa: E402

# Monday 19 to Sunday 25 October 2026.
DAYS = [f"202610{d}" for d in range(19, 26)]
WEEKEND = {"20261024", "20261025"}


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, siri_journey_ref TEXT, stop_sequence INTEGER NOT NULL,
               stop_code TEXT, scheduled_local TEXT, observed_delay_s INTEGER,
               on_time INTEGER, gps_distance_m INTEGER, recorded_at TEXT,
               vehicle_ref TEXT, is_origin INTEGER NOT NULL DEFAULT 0, match_tier TEXT)""")
    audit_rollup.init_summary_tables(conn)
    return conn


def add_day(conn, day, route, delays):
    """One journey per delay value, three readings each; daily rows to match."""
    on_time = early = late = 0
    for n, delay in enumerate(delays):
        for seq in (2, 3, 4):
            conn.execute(
                """INSERT INTO timepoint_observations VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,0,'exact')""",
                (day, "FBRI", route, f"{day}-{route}-{n}", f"J{n}", seq, f"S{seq}",
                 f"{day[:4]}-{day[4:6]}-{day[6:]} 08:0{seq}:00+01:00", delay, 1, 20,
                 f"{day[:4]}-{day[4:6]}-{day[6:]}T07:0{seq}:00+00:00", f"V{n}"))
            band = audit_rollup.delay_band(delay)
            on_time += band == "on_time"; early += band == "early"; late += band == "late"
    readings = 3 * len(delays)
    return {"readings": readings, "on_time": on_time, "early": early, "late": late}


def build(valid=lambda cur, day: True):
    conn = database()
    for day in DAYS:
        totals = {"readings": 0, "on_time": 0, "early": 0, "late": 0}
        routes = {"1": [0, 30, 600, -120, 45]}
        if day not in WEEKEND:
            routes["2"] = [0, 0, 400, 20, 10]
        for route, delays in routes.items():
            counts = add_day(conn, day, route, delays)
            conn.execute(
                """INSERT INTO daily_route_summary (service_date, operator, route,
                       readings_in_gate, on_time, early, late, on_time_pct, mean_delay_s,
                       median_delay_s, readings_total, excluded_distance, expected_trips,
                       observed_trips, coverage_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (day, "FBRI", route, counts["readings"], counts["on_time"], counts["early"],
                 counts["late"], None, 100, None, counts["readings"], 0, 10, 9, 90.0))
            for key in totals:
                totals[key] += counts[key]
        conn.execute(
            """INSERT INTO daily_overall_summary (service_date, operator, readings_in_gate,
                   on_time, early, late, on_time_pct, mean_delay_s, median_delay_s,
                   readings_total, excluded_distance, median_gate_dist_m, expected_trips,
                   observed_trips, coverage_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (day, "FBRI", totals["readings"], totals["on_time"], totals["early"],
             totals["late"], None, 100, None, totals["readings"], 0, None,
             10 * len(routes), 9 * len(routes), 90.0))
        write_day(conn, day, None)
    return conn, audit_period.build_period(conn.cursor(), DAYS, valid)


def test_every_route_that_ran_in_the_window_is_shown_with_pooled_numbers():
    _, period = build()
    assert (period["from"], period["to"], period["days"]) == ("20261019", "20261025", 7)
    routes = {r["route"]: r for r in period["by_operator"]["FBRI"]["routes"]}
    # Route 2 doesn't run at weekends, so the latest day (a Sunday) wouldn't show it.
    assert set(routes) == {"1", "2"}
    assert routes["1"]["readings_in_gate"] == 7 * 15 and routes["2"]["readings_in_gate"] == 5 * 15
    assert routes["2"]["on_time_pct"] == 80.0
    assert routes["2"]["service_days"] == 5


def test_a_weekday_only_route_is_judged_on_the_days_it_ran():
    _, period = build()
    routes = {r["route"]: r for r in period["by_operator"]["FBRI"]["routes"]}
    q = routes["2"]["qualification"]
    assert q["status"] != "unavailable"
    assert "sample_support_unavailable" not in (q.get("reasons") or [])
    assert q["service_days"] == 5


def test_medians_come_from_raw_readings_and_coverage_from_healthy_days_only():
    _, period = build(valid=lambda cur, day: day != "20261025")
    fbri = period["by_operator"]["FBRI"]
    routes = {r["route"]: r for r in fbri["routes"]}
    assert routes["1"]["median_delay_s"] == 30
    assert routes["2"]["median_delay_s"] == 10
    # Route 1 ran 7 days but only 6 had healthy coverage: 6 x 10 timetabled.
    assert (routes["1"]["expected_trips"], routes["1"]["observed_trips"]) == (60, 54)
    assert fbri["overall"]["readings_in_gate"] == 7 * 15 + 5 * 15
    assert period["coverage_days"] == 6


def test_the_page_summary_is_small_and_keeps_the_latest_day():
    conn, _ = build()
    payload = {"area": "WECA", "operators": [{"code": "FBRI", "name": "First"}],
               "days": [{"service_date": d, "by_operator": {"FBRI": {"overall": {"on_time_pct": 50}}}}
                        for d in DAYS]}
    summary = audit_export.build_summary(conn.cursor(), payload, DAYS)
    assert "days" not in summary
    assert summary["latest_day"]["service_date"] == "20261025"
    assert [d["service_date"] for d in summary["days_index"]] == DAYS
    assert summary["period"]["by_operator"]["FBRI"]["routes"]
    assert summary["full_history"] == "audit_data.json"


def test_routes_report_how_many_readings_were_20_minutes_or_more_late():
    conn = database()
    for day in DAYS[:2]:
        counts = add_day(conn, day, "1", [0, 1500, 600])
        conn.execute(
            """INSERT INTO daily_route_summary (service_date, operator, route,
                   readings_in_gate, on_time, early, late, on_time_pct, mean_delay_s,
                   median_delay_s, readings_total, excluded_distance, expected_trips,
                   observed_trips, coverage_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (day, "FBRI", "1", counts["readings"], counts["on_time"], counts["early"],
             counts["late"], None, 100, None, counts["readings"], 0, 10, 9, 90.0))
        # One journey 25 min late (3 readings) and one 10 min late per day.
        conn.execute("INSERT INTO daily_delay_histogram VALUES (?,?,?,?,?)",
                     (day, "FBRI", "1", "late_20plus", 3))
        conn.execute("INSERT INTO daily_delay_histogram VALUES (?,?,?,?,?)",
                     (day, "FBRI", "1", "late_6_10", 3))
    routes, _ = audit_period._routes(conn.cursor(), DAYS[:2], "FBRI", set(DAYS[:2]), {})
    route = next(r for r in routes if r["route"] == "1")
    assert route["late"] == 12
    assert route["very_late"] == 6
