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
    assert "match_tier" in observation_columns
    assert "stale" in poll_columns
    assert "evidence_written" in poll_columns
    assert "evidence_dropped" in poll_columns
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='matching_evidence'").fetchone()

    audit_db.upsert_observation(connection.cursor(), (
        "20260816", "FBRI", "75", "trip", "journey", 1, "stop",
        "2026-08-16T12:00:00+01:00", 120, 1, 20,
        "2026-08-16T11:02:00+00:00", "vehicle", 0, "fuzzy"))
    assert connection.execute(
        "SELECT match_tier FROM timepoint_observations").fetchone()[0] == "fuzzy"

    audit_db.log_poll(connection, "2026-08-16T12:00:00+00:00", True, {
        "vehicles_total": 10, "candidates": 8, "matched": 7,
        "obs_written": 5, "dropped_insane": 2, "stale": 3,
        "evidence_written": 1, "evidence_dropped": 4,
    })
    assert connection.execute(
        """SELECT dropped_insane, stale, evidence_written, evidence_dropped
           FROM poll_log""").fetchone() == (2, 3, 1, 4)
    connection.close()


def _evidence(day: str, sequence: int) -> dict:
    recorded = f"{day[:4]}-{day[4:6]}-{day[6:]}T10:{sequence:02d}:00+00:00"
    return {
        "captured_at": recorded,
        "service_date": day,
        "reasons": {"sanity_rejected"},
        "calculation_reasons": {"sanity_rejected", "not_timing_point"},
        "operator": "FBRI",
        "route": "75",
        "vehicle_ref": f"vehicle-{sequence}",
        "recorded_at": recorded,
        "chosen_trip_id": "T_OUT",
        "match_tier": "fuzzy",
        "candidate_count": 6,
        "chosen": {
            "route_id": "R75F", "service_id": "WK", "direction_id": 0,
            "timetable_edition": "20260601",
        },
        "alternatives": [{"trip_id": f"alternative-{i}"} for i in range(9)],
    }


def test_matching_evidence_is_idempotent_and_daily_bounded():
    connection = audit_db.connect()
    first = _evidence("20260610", 1)
    assert audit_db.write_matching_evidence(
        connection, first, daily_limit=2, total_limit=10) == "written"
    assert audit_db.write_matching_evidence(
        connection, first, daily_limit=2, total_limit=10) == "duplicate"
    assert audit_db.write_matching_evidence(
        connection, _evidence("20260610", 2),
        daily_limit=2, total_limit=10) == "written"
    assert audit_db.write_matching_evidence(
        connection, _evidence("20260610", 3),
        daily_limit=2, total_limit=10) == "daily_limit"
    row = connection.execute(
        """SELECT COUNT(*), alternatives_json, timetable_edition
           FROM matching_evidence ORDER BY captured_at LIMIT 1""").fetchone()
    assert row[0] == 2
    assert row[1].count("trip_id") == audit_db.MATCHING_EVIDENCE_ALTERNATIVES_LIMIT
    assert row[2] == "20260601"


def test_matching_evidence_total_limit_keeps_recent_receipts():
    connection = audit_db.connect()
    for sequence, day in enumerate(
            ("20260610", "20260611", "20260612"), start=1):
        status = audit_db.write_matching_evidence(
            connection, _evidence(day, sequence),
            daily_limit=10, total_limit=2)
    assert status == "total_pruned"
    dates = [row[0] for row in connection.execute(
        "SELECT service_date FROM matching_evidence ORDER BY captured_at")]
    assert dates == ["20260611", "20260612"]
