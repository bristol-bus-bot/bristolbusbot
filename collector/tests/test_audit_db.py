import sqlite3

from collector import audit_db


def test_connect_migrates_existing_poll_and_observation_tables(tmp_path):
    path = tmp_path / "audit.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE timepoint_observations (
            service_date TEXT NOT NULL,
            operator TEXT NOT NULL,
            route TEXT,
            trip_id TEXT NOT NULL,
            siri_journey_ref TEXT,
            stop_sequence INTEGER NOT NULL,
            stop_code TEXT,
            scheduled_local TEXT,
            observed_delay_s INTEGER,
            on_time INTEGER,
            gps_distance_m INTEGER,
            recorded_at TEXT,
            vehicle_ref TEXT,
            PRIMARY KEY (service_date, trip_id, stop_sequence)
        );
        CREATE TABLE poll_log (
            poll_at TEXT PRIMARY KEY, ok INTEGER, vehicles_total INTEGER,
            candidates INTEGER, matched INTEGER, obs_written INTEGER,
            dropped_insane INTEGER
        );
    """)
    old.close()

    connection = audit_db.connect(path)

    observation_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
    poll_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(poll_log)")}
    assert "is_origin" in observation_columns
    assert "stale" in poll_columns

    audit_db.log_poll(connection, "2026-08-16T12:00:00+00:00", True, {
        "vehicles_total": 10, "candidates": 8, "matched": 7,
        "obs_written": 5, "dropped_insane": 2, "stale": 3,
    })
    assert connection.execute(
        "SELECT dropped_insane, stale FROM poll_log").fetchone() == (2, 3)
    connection.close()
