import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

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
    assert "evidence_deduplicated" in poll_columns
    assert "evidence_scope_dropped" in poll_columns
    assert "evidence_quota_dropped" in poll_columns
    assert "evidence_errors" in poll_columns
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='matching_evidence'").fetchone()
    evidence_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(matching_evidence)")}
    assert {"sampling_date", "sampling_band", "sampling_reason",
            "origin_ref", "destination_ref"} <= evidence_columns

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


def test_matching_evidence_spreads_capacity_across_the_day():
    connection = audit_db.connect()
    first = _evidence("20260610", 1)
    first["recorded_at"] = first["captured_at"] = "2026-06-10T00:01:00+00:00"
    first["journey_ref"] = "night-one"
    second = _evidence("20260610", 2)
    second["recorded_at"] = second["captured_at"] = "2026-06-10T00:02:00+00:00"
    second["journey_ref"] = "night-two"
    refused = _evidence("20260610", 3)
    refused["recorded_at"] = refused["captured_at"] = "2026-06-10T00:03:00+00:00"
    refused["journey_ref"] = "night-three"
    daytime = _evidence("20260610", 4)
    daytime["recorded_at"] = daytime["captured_at"] = "2026-06-10T12:04:00+00:00"
    daytime["journey_ref"] = "daytime"

    assert audit_db.write_matching_evidence(
        connection, first, daily_limit=20, total_limit=20, cell_limit=2) == "written"
    assert audit_db.write_matching_evidence(
        connection, second, daily_limit=20, total_limit=20, cell_limit=2) == "written"
    assert audit_db.write_matching_evidence(
        connection, refused, daily_limit=20, total_limit=20,
        cell_limit=2) == "cell_limit"
    assert audit_db.write_matching_evidence(
        connection, daytime, daily_limit=20, total_limit=20,
        cell_limit=2) == "written"
    bands = [row[0] for row in connection.execute(
        "SELECT sampling_band FROM matching_evidence ORDER BY captured_at")]
    assert bands == ["00-03", "00-03", "12-15"]


def test_sampling_uses_recorded_local_day_not_matched_service_date():
    connection = audit_db.connect()
    item = _evidence("20260611", 1)
    item["captured_at"] = item["recorded_at"] = "2026-06-10T10:01:00+00:00"
    item["origin_ref"] = "ORIGIN"
    item["destination_ref"] = "DESTINATION"

    assert audit_db.write_matching_evidence(connection, item) == "written"
    row = connection.execute(
        """SELECT service_date,sampling_date,sampling_band,
                  origin_ref,destination_ref
           FROM matching_evidence""").fetchone()
    assert row == ("20260611", "20260610", "08-11", "ORIGIN", "DESTINATION")


def test_sampling_handles_midnight_bst_and_invalid_timestamps():
    connection = audit_db.connect()
    before_midnight = _evidence("20260610", 1)
    before_midnight["captured_at"] = before_midnight["recorded_at"] = (
        "2026-06-10T22:30:00+00:00")
    after_midnight = _evidence("20260610", 2)
    after_midnight["captured_at"] = after_midnight["recorded_at"] = (
        "2026-06-10T23:30:00+00:00")
    invalid = _evidence("20260610", 3)
    invalid["captured_at"] = invalid["recorded_at"] = "not-a-time"

    assert audit_db.write_matching_evidence(connection, before_midnight) == "written"
    assert audit_db.write_matching_evidence(connection, after_midnight) == "written"
    assert audit_db.write_matching_evidence(connection, invalid) == "invalid_timestamp"
    slots = connection.execute(
        """SELECT sampling_date,sampling_band FROM matching_evidence
           ORDER BY recorded_at""").fetchall()
    assert slots == [("20260610", "20-23"), ("20260611", "00-03")]


def test_sampling_rejects_out_of_scope_and_deduplicates_noisy_journey():
    connection = audit_db.connect()
    outside = _evidence("20260610", 1)
    outside["operator"] = "NATX"
    assert audit_db.write_matching_evidence(
        connection, outside) == "operator_out_of_scope"

    statuses = []
    for sequence in (2, 3, 4):
        item = _evidence("20260610", sequence)
        item["vehicle_ref"] = "same-vehicle"
        statuses.append(audit_db.write_matching_evidence(connection, item))
    assert statuses == ["written", "written", "journey_duplicate"]
    assert connection.execute(
        "SELECT COUNT(*) FROM matching_evidence").fetchone()[0] == 2


def test_sampling_reserves_last_cell_place_for_a_new_journey_reference():
    connection = audit_db.connect()
    for sequence in (1, 2):
        item = _evidence("20260610", sequence)
        item["journey_ref"] = "common"
        assert audit_db.write_matching_evidence(
            connection, item, cell_limit=3, per_journey_limit=10) == "written"
    repeated = _evidence("20260610", 3)
    repeated["journey_ref"] = "common"
    fresh = _evidence("20260610", 4)
    fresh["journey_ref"] = "new"
    assert audit_db.write_matching_evidence(
        connection, repeated, cell_limit=3,
        per_journey_limit=10) == "reserved_slot"
    assert audit_db.write_matching_evidence(
        connection, fresh, cell_limit=3, per_journey_limit=10) == "written"


def test_sampling_prevents_one_audited_operator_filling_a_cell():
    connection = audit_db.connect()
    for sequence in range(1, 5):
        item = _evidence("20260610", sequence)
        item["journey_ref"] = f"FBRI-{sequence}"
        assert audit_db.write_matching_evidence(connection, item) == "written"
    fifth = _evidence("20260610", 5)
    fifth["journey_ref"] = "FBRI-5"
    assert audit_db.write_matching_evidence(
        connection, fifth) == "operator_cell_limit"
    other = _evidence("20260610", 6)
    other["operator"] = "ABUS"
    other["journey_ref"] = "ABUS-1"
    assert audit_db.write_matching_evidence(connection, other) == "written"


@pytest.mark.parametrize("instant,slot", [
    ("2026-03-29T00:59:00Z", ("20260329", "00-03")),
    ("2026-03-29T01:01:00Z", ("20260329", "00-03")),
    ("2026-10-25T00:30:00Z", ("20261025", "00-03")),
    ("2026-10-25T01:30:00Z", ("20261025", "00-03")),
])
def test_sampling_clock_change_does_not_create_an_extra_band(instant, slot):
    item = _evidence("20260610", 1)
    item["recorded_at"] = instant
    assert audit_db._sampling_slot(item, ["extreme_delay"]) == (
        *slot, "extreme_delay")


def test_sampling_falls_back_to_capture_time_not_a_naive_feed_time():
    item = _evidence("20260611", 1)
    item["recorded_at"] = "2026-06-11T10:00:00"
    item["captured_at"] = "2026-06-10T23:30:00Z"
    assert audit_db._sampling_slot(item, ["extreme_delay"]) == (
        "20260611", "00-03", "extreme_delay")


def test_upgrade_and_restart_count_legacy_receipts_by_local_capture_day(tmp_path):
    path = tmp_path / "audit.db"
    connection = audit_db.connect(path)
    legacy = _evidence("20260610", 1)
    legacy["captured_at"] = legacy["recorded_at"] = "2026-06-10T23:30:00Z"
    assert audit_db.write_matching_evidence(connection, legacy) == "written"
    # Recreate the previous durable schema, including a receipt whose UTC
    # date differs from its local date. Upgrade must not reset its allowance.
    connection.execute("DROP INDEX idx_matching_evidence_sampling")
    for name in ("sampling_date", "sampling_band", "sampling_reason",
                 "origin_ref", "destination_ref"):
        connection.execute(f"ALTER TABLE matching_evidence DROP COLUMN {name}")
    connection.commit()
    connection.close()

    daytime = _evidence("20260611", 2)
    for _ in range(2):
        connection = audit_db.connect(path)
        assert audit_db.write_matching_evidence(
            connection, daytime, daily_limit=1) == "daily_limit"
        assert connection.execute(
            "SELECT captured_at FROM matching_evidence").fetchall() == [
                ("2026-06-10T23:30:00Z",)]
        connection.close()


def test_default_limits_survive_sustained_noise_across_a_whole_day():
    connection = audit_db.connect()
    start = datetime(2026, 6, 9, 23, tzinfo=timezone.utc)
    for band in range(6):
        for reason in audit_db.MATCHING_EVIDENCE_REASON_PRIORITY:
            for index in range(100):
                item = _evidence("20260611", 1)  # deliberately wrong match date
                item.update(
                    captured_at=(start + timedelta(hours=band * 4,
                                                   seconds=index)).isoformat(),
                    recorded_at=(start + timedelta(hours=band * 4,
                                                   seconds=index)).isoformat(),
                    vehicle_ref=f"vehicle-{index}", chosen_trip_id=f"trip-{index}",
                    journey_ref=f"ref-{index}", reasons={reason},
                    operator=("FBRI", "ABUS", "LEMB")[index % 3])
                audit_db.write_matching_evidence(connection, item)
    counts = connection.execute(
        "SELECT sampling_band,COUNT(*) FROM matching_evidence "
        "GROUP BY sampling_band ORDER BY sampling_band").fetchall()
    assert counts == [(f"{hour:02d}-{hour+3:02d}", 40)
                      for hour in range(0, 24, 4)]
    assert sum(count for _, count in counts) == 240
    assert connection.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM matching_evidence "
        "GROUP BY sampling_band,sampling_reason,operator)").fetchone()[0] <= 4
