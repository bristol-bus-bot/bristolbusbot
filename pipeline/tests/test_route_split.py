from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_rollup  # noqa: E402
import route_split  # noqa: E402
from audit_operators import NETWORK_LABEL  # noqa: E402

GEO = {
    "bath1": {"area": "Bath and North East Somerset", "ward": "Westmoreland"},
    "bath2": {"area": "Bath and North East Somerset", "ward": "Odd Down"},
    "bris1": {"area": "Bristol", "ward": "Central"},
    "bris2": {"area": "Bristol", "ward": "Clifton"},
}


def database():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE expected_trips (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, direction INTEGER, first_departure TEXT,
               route_id TEXT, first_stop_code TEXT
           )""")
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
               trip_id TEXT NOT NULL, stop_sequence INTEGER NOT NULL,
               stop_code TEXT, scheduled_local TEXT, observed_delay_s INTEGER,
               gps_distance_m INTEGER, is_origin INTEGER NOT NULL DEFAULT 0
           )""")
    return conn


def schedule(conn, day, trip, route, route_id, first_stop, operator="FBRI"):
    conn.execute(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?)",
        (day, operator, route, trip, 0, "08:00:00", route_id, first_stop))


def reading(conn, day, trip, route, stop, delay, operator="FBRI", seq=[0]):
    seq[0] += 1
    conn.execute(
        """INSERT INTO timepoint_observations (service_date, operator, route,
               trip_id, stop_sequence, stop_code, scheduled_local,
               observed_delay_s, gps_distance_m) VALUES (?,?,?,?,?,?,?,?,?)""",
        (day, operator, route, trip, seq[0], stop,
         f"{day[:4]}-{day[4:6]}-{day[6:]} 08:10:00+01:00", delay, 20))


def two_fours(conn, day="20261023"):
    # First's 4 is two routes: one starting in Bath, one in Bristol.
    schedule(conn, day, "BATH-A", "4", "R-BATH", "bath1")
    schedule(conn, day, "BATH-B", "4", "R-BATH", "bath2")
    schedule(conn, day, "BRIS-A", "4", "R-BRIS", "bris1")
    # The 70 only runs in Bristol, even with two route IDs (its directions).
    schedule(conn, day, "SEVENTY-A", "70", "R-70-OUT", "bris1")
    schedule(conn, day, "SEVENTY-B", "70", "R-70-IN", "bris2")
    return day


def test_shared_numbers_get_their_area_and_single_area_numbers_do_not():
    conn = database()
    two_fours(conn)
    split = route_split.build(conn, GEO)
    assert split.shared == {("FBRI", "4")}
    assert split.route("FBRI", "4", "BATH-A") == "4 Bath"
    assert split.route("FBRI", "4", "BRIS-A") == "4 Bristol"
    assert split.route("FBRI", "70", "SEVENTY-A") == "70"
    # Another operator's 4 isn't First's 4.
    assert split.route("SSWL", "4", "BATH-A") == "4"
    assert split.route("FBRI", None, "BATH-A") is None


def test_a_reading_without_a_scheduled_trip_uses_its_stop():
    conn = database()
    two_fours(conn)
    split = route_split.build(conn, GEO)
    assert split.route("FBRI", "4", "UNSCHEDULED", "bath2") == "4 Bath"
    assert split.route("FBRI", "4", "UNSCHEDULED", "nowhere") == "4"
    assert split.route("FBRI", "4", "UNSCHEDULED") == "4"


def test_no_schedule_table_means_no_split():
    conn = sqlite3.connect(":memory:")
    split = route_split.build(conn, GEO)
    assert split.route("FBRI", "4", "anything") == "4"


def test_a_route_crossing_a_boundary_is_not_split():
    # The X14 runs Bristol to Weston. A new timetable edition gave it a new
    # route ID, and each ID's trips start at a different end, but its readings
    # show both IDs serve both areas, so it stays one route.
    conn = database()
    day = "20261023"
    schedule(conn, day, "X14-OLD", "X14", "R-OLD", "bris1")
    schedule(conn, day, "X14-NEW", "X14", "R-NEW", "nsom1")
    for trip in ("X14-OLD", "X14-NEW"):
        reading(conn, day, trip, "X14", "bris2", 0)
        reading(conn, day, trip, "X14", "nsom1", 0)
    split = route_split.build(conn, {**GEO, "nsom1": {"area": "North Somerset", "ward": "Weston"}})
    assert split.shared == set()
    assert split.route("FBRI", "X14", "X14-NEW") == "X14"


def test_the_daily_rollup_reports_each_town_separately(monkeypatch):
    conn = database()
    day = two_fours(conn)
    audit_rollup.init_summary_tables(conn)
    monkeypatch.setattr(route_split, "load_geo_index", lambda: GEO)
    route_split._cache.clear()
    for _ in range(3):
        reading(conn, day, "BATH-A", "4", "bath1", 30)      # on time
        reading(conn, day, "BRIS-A", "4", "bris1", 900)     # 15 min late
        reading(conn, day, "SEVENTY-A", "70", "bris1", 30)
    for label, operators in (("FBRI", ["FBRI"]), (NETWORK_LABEL, ["FBRI", "SSWL"])):
        audit_rollup.rollup(conn, day, operators, label)
    routes = {
        (row[0], row[1]): (row[2], row[3])
        for row in conn.execute(
            """SELECT operator, route, readings_in_gate, on_time_pct
                 FROM daily_route_summary WHERE service_date = ?""", (day,))}
    for label in ("FBRI", NETWORK_LABEL):
        assert routes[(label, "4 Bath")] == (3, 100.0)
        assert routes[(label, "4 Bristol")] == (3, 0.0)
        assert routes[(label, "70")] == (3, 100.0)
        assert (label, "4") not in routes
    # The overall figure counts every reading once either way.
    overall = conn.execute(
        """SELECT readings_in_gate FROM daily_overall_summary
            WHERE service_date = ? AND operator = 'FBRI'""", (day,)).fetchone()
    assert overall[0] == 9


def test_an_unscheduled_reading_joins_the_route_serving_its_area():
    # Bristol's U2 serves Bristol and North Somerset and is labelled after its
    # main area; a stray reading at a Bristol stop belongs to it, not to a new
    # "U2 Bristol".
    conn = database()
    day = "20261023"
    geo = {**GEO, "nsom1": {"area": "North Somerset", "ward": "Blagdon & Churchill"}}
    schedule(conn, day, "U2-BATH", "U2", "R-BATH", "bath1")
    schedule(conn, day, "U2-BRIS", "U2", "R-BRIS", "bris1")
    reading(conn, day, "U2-BATH", "U2", "bath1", 0)
    reading(conn, day, "U2-BRIS", "U2", "bris1", 0)
    for _ in range(3):
        reading(conn, day, "U2-BRIS", "U2", "nsom1", 0)
    split = route_split.build(conn, geo)
    assert split.route("FBRI", "U2", "U2-BRIS") == "U2 N Somerset"
    assert split.route("FBRI", "U2", "STRAY", "bris2") == "U2 N Somerset"
    assert split.route("FBRI", "U2", "STRAY", "bath2") == "U2 Bath"


def test_the_confidence_check_files_evidence_under_the_split_labels(monkeypatch):
    import sample_quality
    conn = database()
    for column in ("vehicle_ref TEXT", "siri_journey_ref TEXT", "recorded_at TEXT"):
        conn.execute(f"ALTER TABLE timepoint_observations ADD COLUMN {column}")
    day = two_fours(conn)
    reading(conn, day, "BATH-A", "4", "bath1", 30)
    reading(conn, day, "BRIS-A", "4", "bris1", 900)
    conn.execute("UPDATE timepoint_observations SET vehicle_ref = 'V1', siri_journey_ref = 'J1', "
                 "recorded_at = '2026-10-23T08:10:00+01:00'")
    monkeypatch.setattr(route_split, "load_geo_index", lambda: GEO)
    route_split._cache.clear()
    sample_quality.write_day(conn, day, GEO)
    keys = {row[0] for row in conn.execute(
        "SELECT scope_key FROM daily_sample_support WHERE operator = 'FBRI' AND scope = 'route'")}
    assert keys == {"4 Bath", "4 Bristol"}
