from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone

import pytest

from deploy import evidence_pack


NOW = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
DAY = "20260823"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def databases(tmp_path):
    audit_path = tmp_path / "audit.db"
    timetable_path = tmp_path / "timetable.db"
    audit = sqlite3.connect(audit_path)
    audit.executescript("""
        CREATE TABLE matching_evidence (
            evidence_id TEXT PRIMARY KEY, captured_at TEXT, service_date TEXT,
            reasons_json TEXT, calculation_reasons_json TEXT, operator TEXT,
            route TEXT, vehicle_ref TEXT, direction TEXT, journey_ref TEXT,
            origin_aimed_departure TEXT, recorded_at TEXT, lat REAL, lon REAL,
            bearing REAL, block_ref TEXT, chosen_trip_id TEXT, match_tier TEXT,
            candidate_count INTEGER, candidates_truncated INTEGER,
            gps_distance_m INTEGER, delay_s INTEGER, event_type TEXT,
            timetable_route_id TEXT, timetable_service_id TEXT,
            timetable_direction_id INTEGER, timetable_edition TEXT,
            alternatives_json TEXT
        );
        CREATE TABLE timepoint_observations (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            siri_journey_ref TEXT, stop_sequence INTEGER, stop_code TEXT,
            scheduled_local TEXT, observed_delay_s INTEGER, on_time INTEGER,
            gps_distance_m INTEGER, recorded_at TEXT, vehicle_ref TEXT,
            is_origin INTEGER, match_tier TEXT
        );
        CREATE TABLE poll_log (
            poll_at TEXT, ok INTEGER, vehicles_total INTEGER,
            candidates INTEGER, matched INTEGER, obs_written INTEGER,
            dropped_insane INTEGER, stale INTEGER, evidence_written INTEGER,
            evidence_dropped INTEGER
        );
        CREATE TABLE expected_trips (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            siri_ref TEXT, direction INTEGER, first_departure TEXT,
            route_id TEXT, service_id TEXT, block_id TEXT,
            vehicle_journey_code TEXT, first_stop_id TEXT,
            first_stop_code TEXT, timetable_edition TEXT,
            last_departure TEXT
        );
    """)
    timetable = sqlite3.connect(timetable_path)
    timetable.executescript("""
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT, route_id TEXT, service_id TEXT,
            trip_headsign TEXT, trip_short_name TEXT, direction_id INTEGER,
            block_id TEXT, vehicle_journey_code TEXT);
        CREATE TABLE stops (
            stop_id TEXT, stop_code TEXT, stop_name TEXT);
        CREATE TABLE stop_times (
            trip_id TEXT, arrival_time TEXT, departure_time TEXT,
            stop_id TEXT, stop_sequence INTEGER, timepoint INTEGER,
            pickup_type INTEGER, drop_off_type INTEGER);
        CREATE TABLE route_service_editions (
            route_id TEXT, edition_start TEXT, effective_end TEXT);
        INSERT INTO agency VALUES ('A', 'FBRI');
        INSERT INTO routes VALUES ('R75', 'A', '75');
        INSERT INTO trips VALUES (
            'trip-1', 'R75', 'WK', 'Centre', '1015', 1, 'BLOCK-1', 'VJC-1');
        INSERT INTO stops VALUES ('STOP-A', '0100A', 'First stop');
        INSERT INTO stops VALUES ('STOP-B', '0100B', 'Second stop');
        INSERT INTO stop_times VALUES (
            'trip-1', '10:15:00', '10:15:00', 'STOP-A', 1, 1, 0, 0);
        INSERT INTO stop_times VALUES (
            'trip-1', '10:25:00', '10:25:00', 'STOP-B', 2, 1, 0, 0);
        INSERT INTO route_service_editions VALUES (
            'R75', '20260801', '20260831');
    """)
    timetable.commit()
    timetable.close()
    return audit_path, timetable_path, audit


def add_receipt(audit, *, index=1, reasons=None, calculation=None,
                alternatives=None, match_tier="exact", candidates=1,
                edition="20260801", trip="trip-1", vehicle="bus-1"):
    captured = f"2026-08-23T10:{index % 60:02d}:00+00:00"
    audit.execute(
        """INSERT INTO matching_evidence VALUES (
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"evidence-{index:03d}", captured, DAY,
         json.dumps(reasons or ["extreme_delay"]),
         json.dumps(calculation or []), "FBRI", "75", vehicle, "inbound",
         "journey-1015", "2026-08-23T10:15:00+01:00", captured,
         51.45, -2.58, 90.0, "BLOCK-1", trip, match_tier, candidates, 0,
         30, 2700, "delayed", "R75", "WK", 1, edition,
         json.dumps(alternatives or [])))


def add_related_rows(audit):
    audit.execute(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DAY, "FBRI", "75", "trip-1", "journey-1015", 2, "0100B",
         "10:25:00", 300, 0, 20, "2026-08-23T10:01:00+00:00",
         "bus-1", 0, "exact"))
    audit.execute(
        "INSERT INTO poll_log VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-23T10:01:00+00:00", 1, 100, 90, 88, 30, 0, 2, 1, 0))
    audit.execute(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DAY, "FBRI", "75", "trip-1", "1015", 1, "10:15:00",
         "R75", "WK", "BLOCK-1", "VJC-1", "STOP-A", "0100A",
         "20260801", "10:25:00"))


def finish(audit):
    audit.commit()
    audit.close()


def test_bundle_joins_saved_and_related_evidence_without_writing_databases(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(
        audit, reasons=["match_changed_within_run", "direction_changed_within_run"],
        alternatives=[{
            "trip_id": "trip-2", "route_id": "R75", "service_id": "WK",
            "direction_id": 0, "timetable_edition": "20260801",
        }])
    add_related_rows(audit)
    finish(audit)
    before = (file_hash(audit_path), file_hash(timetable_path))

    result = evidence_pack.build_bundle(
        audit_path, timetable_path, {"date": DAY, "bus": "bus-1"}, now=NOW)

    assert result["mode"] == "private_read_only_diagnostic"
    assert result["summary"]["matching_receipts"] == 1
    item = result["incidents"][0]
    assert item["assessment"]["likely_cause"] == "wrong_journey"
    assert item["matching_decision"]["chosen"]["trip_id"] == "trip-1"
    assert item["matching_decision"]["other_journeys_considered"][0][
        "trip_id"] == "trip-2"
    assert item["related_audit_observations"]["rows"][0][
        "observed_delay_s"] == 300
    assert item["scheduled_trip_snapshot"]["row"]["block_id"] == "BLOCK-1"
    assert item["current_timetable_journey"]["trip"]["agency_noc"] == "FBRI"
    assert len(item["current_timetable_journey"]["stops"]) == 2
    assert item["nearby_collector_polls"]["rows"][0]["ok"] == 1
    assert before == (file_hash(audit_path), file_hash(timetable_path))


def test_broad_selection_is_bounded_and_spread_across_time(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    for index in range(30):
        add_receipt(audit, index=index)
    finish(audit)

    result = evidence_pack.build_bundle(
        audit_path, timetable_path, {"date": DAY}, now=NOW)

    assert result["summary"] == {
        "plain_english": (
            "Found 30 saved odd-reading receipts. Included 25 spread across "
            "the selected time range. Current labels: real_delay 25."),
        "matching_receipts": 30,
        "included_receipts": 25,
        "receipts_truncated": True,
        "receipt_selection": "evenly_spaced_across_time",
        "likely_cause_counts": {"real_delay": 25},
    }
    assert result["incidents"][0]["evidence_id"] == "evidence-000"
    assert result["incidents"][-1]["evidence_id"] == "evidence-029"
    assert len(evidence_pack.serialised(result)) < evidence_pack.MAX_OUTPUT_BYTES


@pytest.mark.parametrize(("reasons", "calculation", "alternatives", "category"), [
    (["direction_changed_within_run"], [], [], "wrong_direction"),
    (["extreme_delay"], ["outside_measurement_gate"], [], "gps_problem"),
    (["sanity_rejected"], ["sanity_rejected"], [], "clock_problem"),
    (["extreme_delay"], [], [{"timetable_edition": "20260701"}],
     "timetable_overlap"),
])
def test_plain_cause_categories_are_conservative(
        tmp_path, reasons, calculation, alternatives, category):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(audit, reasons=reasons, calculation=calculation,
                alternatives=alternatives)
    finish(audit)

    result = evidence_pack.build_bundle(
        audit_path, timetable_path, {"evidence_id": "evidence-001"}, now=NOW)

    assessment = result["incidents"][0]["assessment"]
    assert assessment["likely_cause"] == category
    assert assessment["not_assessable_from_saved_receipt"] == [
        "old_repeated_data"]


def test_missing_old_context_is_labelled_instead_of_reconstructed(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(audit, trip="old-trip")
    finish(audit)

    result = evidence_pack.build_bundle(
        audit_path, timetable_path, {"trip": "old-trip"}, now=NOW)
    item = result["incidents"][0]

    assert item["related_audit_observations"]["available"] is False
    assert item["scheduled_trip_snapshot"]["available"] is False
    assert item["current_timetable_journey"]["available"] is False
    assert "never substituted" in item["current_timetable_journey"]["note"]


def test_cli_creates_private_file_and_does_not_overwrite_by_default(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(audit)
    finish(audit)
    output = tmp_path / "reading.json"
    args = [
        "--date", "2026-08-23", "--output", str(output),
        "--audit-db", str(audit_path), "--timetable-db", str(timetable_path),
    ]

    assert evidence_pack.main(args) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1
    # Windows only exposes its read-only bit through chmod; production is Linux.
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    original = output.read_bytes()
    assert evidence_pack.main(args) == 2
    assert output.read_bytes() == original


def test_cli_refuses_public_path_and_no_match_without_writing(tmp_path):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(audit)
    finish(audit)
    public = tmp_path / "public_html"
    public.mkdir()
    unsafe = public / "reading.json"
    common = ["--audit-db", str(audit_path), "--timetable-db", str(timetable_path)]

    assert evidence_pack.main([
        "--date", DAY, "--output", str(unsafe), *common]) == 2
    assert not unsafe.exists()
    missing = tmp_path / "missing.json"
    assert evidence_pack.main([
        "--trip", "does-not-exist", "--output", str(missing), *common]) == 2
    assert not missing.exists()


def test_atomic_writer_enforces_hard_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_pack, "MAX_OUTPUT_BYTES", 10)
    output = tmp_path / "too-large.json"

    with pytest.raises(evidence_pack.EvidencePackError, match="safety limit"):
        evidence_pack.atomic_private_json(output, {"larger": "than ten bytes"})

    assert not output.exists()


def test_sized_bundle_reduces_broad_sample_before_hitting_byte_limit(
        tmp_path, monkeypatch):
    audit_path, timetable_path, audit = databases(tmp_path)
    add_receipt(audit, index=1)
    add_receipt(audit, index=2)
    finish(audit)
    selectors = {"date": DAY}
    one = evidence_pack.build_bundle(
        audit_path, timetable_path, selectors, now=NOW, receipt_limit=1,
        requested_receipt_limit=2)
    two = evidence_pack.build_bundle(
        audit_path, timetable_path, selectors, now=NOW, receipt_limit=2,
        requested_receipt_limit=2)
    limit = (len(evidence_pack.serialised(one))
             + len(evidence_pack.serialised(two))) // 2
    monkeypatch.setattr(evidence_pack, "MAX_OUTPUT_BYTES", limit)

    result = evidence_pack.build_sized_bundle(
        audit_path, timetable_path, selectors, now=NOW, receipt_limit=2)

    assert result["summary"]["matching_receipts"] == 2
    assert result["summary"]["included_receipts"] == 1
    assert result["limits"]["requested_receipt_limit"] == 2
    assert result["limits"]["receipt_limit"] == 1
    assert len(evidence_pack.serialised(result)) <= limit
