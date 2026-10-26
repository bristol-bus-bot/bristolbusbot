from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_segments  # noqa: E402
from audit_operators import public_route  # noqa: E402

# 28 days from Monday 28 September 2026: four full weeks.
WINDOW = [f"202609{d}" for d in (28, 29, 30)] + [f"202610{d:02d}" for d in range(1, 26)]


def database(tmp_path):
    audit = sqlite3.connect(tmp_path / "audit.db")
    audit.row_factory = sqlite3.Row
    audit.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
               stop_sequence INTEGER, stop_code TEXT, scheduled_local TEXT,
               observed_delay_s INTEGER, gps_distance_m INTEGER,
               is_origin INTEGER NOT NULL DEFAULT 0)""")
    tt = sqlite3.connect(tmp_path / "tt.db")
    tt.row_factory = sqlite3.Row
    tt.executescript(
        """CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
           CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
           CREATE TABLE trips (trip_id TEXT, route_id TEXT, direction_id INTEGER);
           CREATE TABLE stops (stop_id TEXT, stop_code TEXT, stop_name TEXT);
           CREATE TABLE stop_times (trip_id TEXT, stop_sequence INTEGER, stop_id TEXT, timepoint INTEGER);
           INSERT INTO agency VALUES ('OP', 'FBRI');
           INSERT INTO routes VALUES ('R1', 'OP', '1');""")
    for code, name in (("A", "Temple Meads"), ("B", "The Centre"), ("C", "Broadmead")):
        tt.execute("INSERT INTO stops VALUES (?,?,?)", (code, code, name))
    return audit, tt


def journey(audit, tt, day, trip, delays, times=("08:00", "08:07", "08:15")):
    tt.execute("INSERT INTO trips VALUES (?, 'R1', 0)", (trip,))
    for seq, (code, hhmm, delay) in enumerate(zip("ABC", times, delays), 1):
        tt.execute("INSERT INTO stop_times VALUES (?,?,?,1)", (trip, seq, code))
        if delay is None:
            continue
        audit.execute(
            "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,0)",
            (day, "FBRI", "1", trip, seq, code,
             f"{day[:4]}-{day[4:6]}-{day[6:]} {hhmm}:00+01:00", delay, 20))


def roll(tmp_path, audit, tt, days):
    audit.commit(); tt.commit()
    for day in days:
        audit_segments.rollup_day(audit, tt, day)
    return audit_segments.build(audit, WINDOW, "ALL", public_route)


def test_steady_tight_stretch_is_published_with_plain_numbers(tmp_path):
    audit, tt = database(tmp_path)
    weekdays = [d for d in WINDOW if d not in {"20261003", "20261004", "20261010", "20261011",
                                                "20261017", "20261018", "20261024", "20261025"}]
    for i, day in enumerate(weekdays):
        for n in range(2):
            # 7 minutes timetabled A->B, about 14 taken; B->C runs to time.
            journey(audit, tt, day, f"T{i}-{n}", (0, 420, 420))
    out = roll(tmp_path, audit, tt, weekdays)
    items = out["FBRI"]["routes"]["1"]
    assert [(i["from"], i["to"], i["kind"]) for i in items] == [("Temple Meads", "The Centre", "tight")]
    item = items[0]
    assert (item["timetable_min"], item["typical_min"], item["journeys"], item["share_pct"]) == (7.0, 14.0, 40, 100)
    assert (item["band"], item["day_type"]) == ("am_peak", "weekday")
    assert out["FBRI"]["worst"][0]["route"] == "1"
    assert "1" in out["ALL"]["routes"]


def test_one_bad_week_is_not_a_pattern(tmp_path):
    audit, tt = database(tmp_path)
    days = ["20261005", "20261006", "20261007", "20261008", "20261009",
            "20261012", "20261013", "20261014", "20261015", "20261016"]
    for i, day in enumerate(days):
        for n in range(4):
            journey(audit, tt, day, f"T{i}-{n}", (0, 420 if i < 5 else 0, None))
    out = roll(tmp_path, audit, tt, days)
    assert out == {} or not out["FBRI"]["routes"]


def test_buses_waiting_and_recovery_time_are_labelled_not_blamed(tmp_path):
    audit, tt = database(tmp_path)
    days = [d for d in WINDOW if d >= "20261005" and d <= "20261023"]
    for i, day in enumerate(days):
        for n in range(3):
            # Early at A (waiting there), late at B; then a little early
            # into the terminus C, which is recovery time.
            journey(audit, tt, day, f"T{i}-{n}", (-240, 180, 60))
    out = roll(tmp_path, audit, tt, days)
    kinds = {(i["from"], i["to"]): i["kind"] for i in out["FBRI"]["routes"]["1"]}
    assert kinds == {("Temple Meads", "The Centre"): "waiting"}
    assert out["FBRI"]["recovery_counts"]["1"] >= 1
    assert out["FBRI"]["worst"] == []


def test_rollup_day_replaces_rows(tmp_path):
    audit, tt = database(tmp_path)
    journey(audit, tt, "20261005", "T1", (0, 60, 120))
    audit.commit(); tt.commit()
    assert audit_segments.rollup_day(audit, tt, "20261005") == 2
    assert audit_segments.rollup_day(audit, tt, "20261005") == 2
    rows = audit.execute("SELECT from_stop, to_stop, to_terminus, changes_json FROM daily_segment_changes ORDER BY from_stop").fetchall()
    assert [tuple(r) for r in rows] == [("A", "B", 0, "[60]"), ("B", "C", 1, "[60]")]
