from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import segment_analysis  # noqa: E402


def databases(tmp_path):
    audit = sqlite3.connect(tmp_path / "audit.db")
    audit.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
               stop_sequence INTEGER, stop_code TEXT, scheduled_local TEXT,
               observed_delay_s INTEGER, gps_distance_m INTEGER,
               is_origin INTEGER NOT NULL DEFAULT 0)""")
    tt = sqlite3.connect(tmp_path / "tt.db")
    tt.execute("CREATE TABLE trips (trip_id TEXT, direction_id INTEGER)")
    tt.execute("CREATE TABLE stop_times (trip_id TEXT, stop_sequence INTEGER, timepoint INTEGER)")
    return audit, tt


def journey(audit, tt, trip, date, readings, *, timing=(1, 5, 9, 14), direction=0):
    """readings: (stop_sequence, 'HH:MM', delay_s[, distance])."""
    tt.execute("INSERT INTO trips VALUES (?,?)", (trip, direction))
    for seq in range(1, 15):
        tt.execute("INSERT INTO stop_times VALUES (?,?,?)", (trip, seq, int(seq in timing)))
    for seq, hhmm, delay, *dist in readings:
        audit.execute(
            "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,0)",
            (date, "FBRI", "43", trip, seq, f"S{seq}",
             f"{date[:4]}-{date[4:6]}-{date[6:]} {hhmm}:00+01:00", delay, dist[0] if dist else 20))


def run(tmp_path, audit, tt, **kwargs):
    audit.commit(); tt.commit()
    return segment_analysis.segment_pairs(
        segment_analysis.read_only(tmp_path / "audit.db"),
        segment_analysis.read_only(tmp_path / "tt.db"), **kwargs)


def test_pairs_only_adjacent_timing_points_and_never_bridge_a_gap(tmp_path):
    audit, tt = databases(tmp_path)
    # Readings at timing points 5, 9 and 14: 5->9 and 9->14 are stretches.
    journey(audit, tt, "A", "20261021", [(5, "08:00", 60), (9, "08:06", 240), (14, "08:15", 180)])
    # Timing point 9 missing: 5->14 must not be measured.
    journey(audit, tt, "B", "20261021", [(5, "08:00", 60), (14, "08:15", 300)])
    # A reading outside the 150 m gate counts as missing.
    journey(audit, tt, "C", "20261021", [(5, "08:00", 0), (9, "08:06", 30, 400), (14, "08:15", 90)])
    pairs, tally = run(tmp_path, audit, tt)
    stretches = [(p["from_stop"], p["to_stop"], p["change_s"], p["planned_s"], p["actual_s"]) for p in pairs]
    assert stretches == [("S5", "S9", 180, 360, 540), ("S9", "S14", -60, 540, 480)]
    assert tally["gap_skipped"] == 2 and tally["pairs"] == 2


def test_groups_by_day_type_and_band_and_drop_small_groups(tmp_path):
    audit, tt = databases(tmp_path)
    n = 0
    # 30 weekday am-peak journeys losing 3 min on 5->9; 29 weekend ones (dropped).
    for i in range(30):
        n += 1
        journey(audit, tt, f"W{n}", "20261021", [(5, "08:00", 0), (9, "08:06", 180 + i)])
    for i in range(29):
        n += 1
        journey(audit, tt, f"E{n}", "20261024", [(5, "08:00", 0), (9, "08:06", -90)])
    pairs, _ = run(tmp_path, audit, tt)
    groups = segment_analysis.summarise(pairs)
    assert len(groups) == 1
    g = groups[0]
    assert (g["day_type"], g["band"], g["journeys"]) == ("weekday", "am_peak", 30)
    assert g["timetable_min"] == 6.0 and g["typical_actual_min"] == 9.2
    assert g["lost_over_2min_pct"] == 100.0 and g["gained_over_1min_pct"] == 0.0
    assert len(segment_analysis.summarise(pairs, min_journeys=29)) == 2


def test_date_window_and_journeys_missing_from_the_timetable(tmp_path):
    audit, tt = databases(tmp_path)
    journey(audit, tt, "A", "20261001", [(5, "08:00", 0), (9, "08:06", 60)])
    journey(audit, tt, "B", "20261020", [(5, "08:00", 0), (9, "08:06", 60)])
    audit.execute(
        "INSERT INTO timepoint_observations VALUES ('20261020','FBRI','43','GONE',5,'S5','2026-10-20 08:00:00+01:00',0,20,0)")
    audit.execute(
        "INSERT INTO timepoint_observations VALUES ('20261020','FBRI','43','GONE',9,'S9','2026-10-20 08:06:00+01:00',0,20,0)")
    pairs, tally = run(tmp_path, audit, tt, date_from="20261010")
    assert len(pairs) == 1 and tally["no_timetable"] == 1


def test_bands_follow_the_scheduled_time_at_the_first_stop():
    assert segment_analysis.band_for(6) == "early"
    assert segment_analysis.band_for(7) == "am_peak"
    assert segment_analysis.band_for(9) == "am_peak"
    assert segment_analysis.band_for(10) == "interpeak"
    assert segment_analysis.band_for(18) == "pm_peak"
    assert segment_analysis.band_for(19) == "evening"


def test_rebuilt_timetable_falls_back_to_stop_codes_conservatively(tmp_path):
    audit, tt = databases(tmp_path)
    tt.execute("ALTER TABLE trips ADD COLUMN route_id TEXT")
    tt.execute("ALTER TABLE stop_times ADD COLUMN stop_id TEXT")
    tt.execute("CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT)")
    tt.execute("CREATE TABLE agency (agency_id TEXT, agency_noc TEXT)")
    tt.execute("CREATE TABLE stops (stop_id TEXT, stop_code TEXT, stop_name TEXT)")
    tt.execute("INSERT INTO agency VALUES ('OP', 'FBRI')")
    tt.execute("INSERT INTO routes VALUES ('R43', 'OP', '43')")
    for code in ("S5", "S7", "S9", "S14"):
        tt.execute("INSERT INTO stops VALUES (?,?,?)", (code, code, f"Stop {code}"))

    def current(trip, codes, direction=0):
        tt.execute("INSERT INTO trips (trip_id, direction_id, route_id) VALUES (?,?,'R43')", (trip, direction))
        for seq, code in enumerate(codes, 1):
            tt.execute("INSERT INTO stop_times VALUES (?,?,1,?)", (trip, seq, code))

    current("NEW1", ["S5", "S9", "S14"])
    current("NEW2", ["S5", "S9", "S14"])
    current("NEW3", ["S5", "S7", "S14"])  # a variant with S7 between S5 and S14
    # Old journey ids, gone from the rebuilt timetable.
    for trip in ("OLD1", "OLD2"):
        for seq, code, hhmm, delay in ((5, "S5", "08:00", 0), (9, "S9", "08:06", 120), (14, "S14", "08:15", 60)):
            audit.execute(
                "INSERT INTO timepoint_observations VALUES ('20261001','FBRI','43',?,?,?,?,?,20,0)",
                (trip, seq, code, f"2026-10-01 {hhmm}:00+01:00", delay))
        # and a journey that read S5 then S14 only: S7 or S9 lies between.
        audit.execute(
            "INSERT INTO timepoint_observations VALUES ('20261001','FBRI','43',?,5,'S5','2026-10-01 08:00:00+01:00',0,20,0)",
            (trip + "x",))
        audit.execute(
            "INSERT INTO timepoint_observations VALUES ('20261001','FBRI','43',?,14,'S14','2026-10-01 08:15:00+01:00',0,20,0)",
            (trip + "x",))
    pairs, tally = run(tmp_path, audit, tt)
    assert sorted({(p["from_stop"], p["to_stop"]) for p in pairs}) == [("S5", "S9"), ("S9", "S14")]
    assert tally["by_stop_code"] == 4 and tally["by_trip"] == 0
    assert tally["code_unconfirmed"] == 2


def test_negative_running_time_is_excluded(tmp_path):
    audit, tt = databases(tmp_path)
    # 6 minutes timetabled, but 10 minutes "gained": the bus would arrive
    # before it left, so the reading at S9 belongs to another visit.
    journey(audit, tt, "A", "20261021", [(5, "08:00", 300), (9, "08:06", -300)])
    pairs, tally = run(tmp_path, audit, tt)
    assert pairs == [] and tally["impossible"] == 1
