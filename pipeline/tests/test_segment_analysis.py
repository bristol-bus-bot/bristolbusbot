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
