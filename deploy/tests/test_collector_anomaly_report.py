from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from deploy import collector_anomaly_report as report


NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def databases(tmp_path):
    audit_path = tmp_path / "audit.db"
    timetable_path = tmp_path / "timetable.db"
    audit = sqlite3.connect(audit_path)
    audit.executescript("""
        CREATE TABLE timepoint_observations (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            stop_sequence INTEGER, stop_code TEXT, scheduled_local TEXT,
            observed_delay_s INTEGER, gps_distance_m INTEGER,
            recorded_at TEXT, vehicle_ref TEXT
        );
        CREATE TABLE poll_log (
            poll_at TEXT, ok INTEGER, vehicles_total INTEGER,
            candidates INTEGER, matched INTEGER, obs_written INTEGER,
            dropped_insane INTEGER
        );
    """)
    timetable = sqlite3.connect(timetable_path)
    timetable.execute(
        "CREATE TABLE stops (stop_code TEXT, stop_lat REAL, stop_lon REAL)")
    timetable.executemany("INSERT INTO stops VALUES (?,?,?)", [
        ("A", 51.4545, -2.5879),
        ("B", 51.5545, -2.5879),
        ("C", 51.4545, -2.3879),
    ])
    timetable.commit()
    timetable.close()
    return audit_path, timetable_path, audit


def add_observation(connection, *, minutes, trip, sequence, stop,
                    delay=0, distance=20, vehicle="vehicle-1", route="1"):
    recorded = NOW - timedelta(minutes=minutes)
    connection.execute(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("20260811", "FBRI", route, trip, sequence, stop, "12:00:00",
         delay, distance, recorded.isoformat(), vehicle))


def test_known_68_minute_delay_and_wrong_direction_are_detected(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_observation(audit, minutes=20, trip="trip-1", sequence=8, stop="A",
                    delay=68 * 60)
    add_observation(audit, minutes=10, trip="trip-1", sequence=3, stop="B")
    audit.commit()
    audit.close()

    result = report.generate_report(audit_path, timetable_path, now=NOW)

    assert result["detectors"]["extreme_delays"]["count"] == 1
    assert result["detectors"]["extreme_delays"]["evidence"][0][
        "observed_delay_s"] == 68 * 60
    assert result["detectors"]["backwards_stop_progress"]["count"] == 1


def test_impossible_speed_overlap_and_poll_changes_are_reported(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_observation(audit, minutes=15, trip="trip-a", sequence=1, stop="A")
    add_observation(audit, minutes=14, trip="trip-b", sequence=1, stop="C")
    add_observation(audit, minutes=13, trip="trip-a", sequence=2, stop="B")
    audit.executemany("INSERT INTO poll_log VALUES (?,?,?,?,?,?,?)", [
        ((NOW - timedelta(hours=30)).isoformat(), 1, 10, 8, 4, 4, 1),
        ((NOW - timedelta(hours=2)).isoformat(), 1, 10, 10, 8, 8, 0),
    ])
    audit.commit()
    audit.close()

    result = report.generate_report(audit_path, timetable_path, now=NOW)

    assert result["detectors"]["impossible_implied_speeds"]["count"] >= 1
    assert result["detectors"]["overlapping_vehicle_trips"]["count"] == 1
    metrics = result["poll_metrics"]
    assert metrics["older_half"]["match_rate"] == 0.5
    assert metrics["recent_half"]["match_rate"] == 0.8
    assert metrics["recent_minus_older"]["match_rate"] == 0.3


def test_evidence_is_bounded_but_full_count_is_retained(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    for index in range(report.EVIDENCE_LIMIT + 7):
        add_observation(
            audit, minutes=index + 1, trip=f"trip-{index}", sequence=1,
            stop="A", delay=61 * 60, vehicle=f"vehicle-{index}")
    audit.commit()
    audit.close()

    result = report.generate_report(audit_path, timetable_path, now=NOW)
    extreme = result["detectors"]["extreme_delays"]

    assert extreme["count"] == report.EVIDENCE_LIMIT + 7
    assert len(extreme["evidence"]) == report.EVIDENCE_LIMIT
    assert extreme["evidence_truncated"] is True
