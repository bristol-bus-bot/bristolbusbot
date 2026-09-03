from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collector_research_export as exporter
import get_collector_research_export as downloader


def audit_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "audit.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE timepoint_observations (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            siri_journey_ref TEXT, stop_sequence INTEGER, stop_code TEXT,
            scheduled_local TEXT, observed_delay_s INTEGER, on_time INTEGER,
            gps_distance_m INTEGER, recorded_at TEXT, vehicle_ref TEXT,
            is_origin INTEGER, match_tier TEXT, secret_future_column TEXT
        );
        CREATE TABLE poll_log (
            poll_at TEXT, ok INTEGER, vehicles_total INTEGER, candidates INTEGER,
            matched INTEGER, obs_written INTEGER, dropped_insane INTEGER,
            stale INTEGER, evidence_written INTEGER, evidence_dropped INTEGER
        );
        CREATE TABLE expected_trips (
            service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
            siri_ref TEXT, direction INTEGER, first_departure TEXT,
            route_id TEXT, service_id TEXT, block_id TEXT,
            vehicle_journey_code TEXT, first_stop_id TEXT,
            first_stop_code TEXT, timetable_edition TEXT, last_departure TEXT
        );
        CREATE TABLE matching_evidence (
            evidence_id TEXT, captured_at TEXT, service_date TEXT,
            reasons_json TEXT, calculation_reasons_json TEXT,
            operator TEXT, route TEXT, vehicle_ref TEXT, direction TEXT,
            journey_ref TEXT, origin_aimed_departure TEXT, recorded_at TEXT,
            lat REAL, lon REAL, bearing REAL, block_ref TEXT,
            chosen_trip_id TEXT, match_tier TEXT, candidate_count INTEGER,
            candidates_truncated INTEGER, gps_distance_m INTEGER,
            delay_s INTEGER, event_type TEXT, timetable_route_id TEXT,
            timetable_service_id TEXT, timetable_direction_id INTEGER,
            timetable_edition TEXT, alternatives_json TEXT
        );
        CREATE TABLE daily_fleet_summary (
            service_date TEXT, operator TEXT, model TEXT, electric INTEGER,
            fuel TEXT, vehicles INTEGER, readings_in_gate INTEGER,
            on_time INTEGER, on_time_pct REAL, mean_delay_s INTEGER,
            median_delay_s INTEGER, routes_json TEXT
        );
        """
    )
    observations = [
        ("20260830", "FBRI", "1", "trip-a", "journey-a", 1, "stop-a",
         "2026-08-30T10:00:00+01:00", -300, 0, 15,
         "2026-08-30T09:55:00Z", "vehicle-a", 1, "exact", "DO NOT EXPORT"),
        ("20260830", "FBRI", "1", "trip-a", "journey-a", 2, "stop-b",
         "2026-08-30T10:10:00+01:00", 120, 1, 12,
         "2026-08-30T09:12:00Z", "vehicle-a", 0, "exact", "DO NOT EXPORT"),
        ("20260831", "FBRI", "2", "trip-b", "journey-b", 3, "stop-c",
         "2026-08-31T12:00:00+01:00", 900, 0, 25,
         "2026-08-31T11:15:00Z", "vehicle-b", 0, "fuzzy", "DO NOT EXPORT"),
    ]
    connection.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        observations)
    connection.executemany(
        "INSERT INTO poll_log VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("2026-08-30T09:00:00Z", 1, 100, 90, 80, 20, 0, 2, 1, 0),
            ("2026-08-31T11:00:00Z", 0, 0, 0, 0, 0, 0, 0, 0, 0),
        ])
    connection.executemany(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("20260830", "FBRI", "1", "trip-a", "journey-a", 0, "10:00:00",
             "route-a", "service-a", "block-a", "vj-a", "sid-a", "stop-a",
             "20260801", "11:00:00"),
            ("20260831", "FBRI", "2", "trip-b", "journey-b", 1, "12:00:00",
             "route-b", "service-b", "block-b", "vj-b", "sid-b", "stop-c",
             "20260801", "13:00:00"),
        ])
    alternatives = [{
        "trip_id": "trip-alt", "route_id": "route-alt",
        "service_id": "service-alt", "direction_id": 1,
        "block_id": "block-alt", "vehicle_journey_code": "vj-alt",
        "route": "1", "origin_departure": "10:05:00",
        "calendar_start": "20260801", "calendar_end": "20260831",
        "gps_distance_m": 22, "timetable_edition": "20260801",
        "unexpected_nested_secret": {"must": "not escape"},
    }]
    connection.execute(
        "INSERT INTO matching_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("evidence-a", "2026-08-30T09:12:00Z", "20260830",
         json.dumps(["extreme_delay"]), json.dumps(["accepted"]),
         "FBRI", "1", "vehicle-a", "outbound", "journey-a", "10:00:00",
         "2026-08-30T09:12:00Z", 51.45, -2.58, 180.0, "block-a",
         "trip-a", "exact", 2, 0, 12, 120, "update", "route-a",
         "service-a", 0, "20260801", json.dumps(alternatives)))
    connection.execute(
        "INSERT INTO daily_fleet_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("20260830", "ALL", "ADL Enviro100EV", 1, "Electric", 4,
         120, 90, 75.0, 30, 10, json.dumps([["24", 80], ["42", 40]])))
    connection.commit()
    connection.close()
    return path


def make_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Path]:
    audit = audit_fixture(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    result = exporter.create_export(
        audit_db=audit, export_root=root, lock_path=tmp_path / "heavy.lock",
        request_id="0123456789ab", from_value="20260830", to_value="20260831",
        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    return result, root / result["remote_filename"]


def extract_database(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source, \
            source.open(exporter.DATABASE_MEMBER) as raw, destination.open("wb") as out:
        out.write(raw.read())


def test_full_census_is_private_typed_documented_and_verifiable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = audit_fixture(tmp_path)
    source_hash = downloader.sha256_file(source)
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    result = exporter.create_export(
        audit_db=source, export_root=root, lock_path=tmp_path / "heavy.lock",
        request_id="0123456789ab", from_value="20260830", to_value="20260831",
        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    archive = root / result["remote_filename"]

    assert source_hash == downloader.sha256_file(source)
    assert result["source_read_only"] is True
    assert result["source_connection_total_changes"] == 0
    assert result["selection"] == "complete_census_no_sampling"
    assert result["row_counts"] == {
        "timepoint_observations": 3,
        "poll_log": 2,
        "expected_trips": 2,
        "matching_evidence": 1,
        "daily_fleet_summary": 1,
    }
    assert result["derived_row_counts"] == {
        "matching_evidence_reasons": 2,
        "matching_evidence_alternatives": 1,
        "daily_fleet_routes": 2,
    }
    if os.name != "nt":
        assert stat.S_IMODE(archive.stat().st_mode) & 0o077 == 0
    manifest = downloader.validate_archive(archive, expected=result)
    assert manifest["date_from"] == "20260830"
    assert manifest["date_to"] == "20260831"

    database = tmp_path / "research.sqlite"
    extract_database(archive, database)
    connection = sqlite3.connect(database)
    exported_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
    evidence_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(matching_evidence)")}
    assert "secret_future_column" not in exported_columns
    assert {"lat", "lon", "reasons_json", "calculation_reasons_json",
            "alternatives_json"}.isdisjoint(evidence_columns)
    assert connection.execute(
        "SELECT reason_kind,reason FROM matching_evidence_reasons "
        "ORDER BY reason_kind").fetchall() == [
            ("calculation", "accepted"), ("selection", "extreme_delay")]
    alternative_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(matching_evidence_alternatives)")}
    assert "unexpected_nested_secret" not in alternative_columns
    assert connection.execute(
        "SELECT route,readings FROM daily_fleet_routes ORDER BY route").fetchall() == [
            ("24", 80), ("42", 40)]
    caveats = dict(connection.execute(
        "SELECT code,plain_english FROM research_caveats"))
    assert "damaged_july_first" in caveats
    assert "expected_trip_denominator_untrusted" in caveats
    dictionary_count = connection.execute(
        "SELECT COUNT(*) FROM research_data_dictionary").fetchone()[0]
    assert dictionary_count > 50
    connection.close()

    with zipfile.ZipFile(archive) as zipped:
        readme = zipped.read(exporter.README_MEMBER).decode()
    assert "complete census; no rows were sampled" in readme
    assert "1 July is a damaged partial day" in readme
    assert "expected_trips is unstable" in readme


@pytest.mark.parametrize("date_from", ["20260714", "20260830"])
def test_comparison_guidance_does_not_certify_a_date_cutoff(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, date_from: str):
    source = audit_fixture(tmp_path)
    if date_from == "20260714":
        with sqlite3.connect(source) as connection:
            connection.execute(
                "UPDATE timepoint_observations SET service_date=? WHERE is_origin=1",
                (date_from,))
    source_hash = downloader.sha256_file(source)
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    result = exporter.create_export(
        audit_db=source, export_root=root, lock_path=tmp_path / "heavy.lock",
        request_id="0123456789ab", from_value=date_from, to_value="20260831",
        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    archive = root / result["remote_filename"]
    downloader.validate_archive(archive, expected=result)
    database = tmp_path / "research.sqlite"
    extract_database(archive, database)
    with sqlite3.connect(database) as connection:
        manifest = dict(connection.execute("SELECT key,value FROM research_manifest"))
        assert manifest["recommended_general_comparison_from"] == "none"
        assert manifest["comparison_guidance_version"] == "2"
        assert manifest["comparison_status"] == "requires_investigation"
        days = connection.execute(
            "SELECT service_date,recommended_general_comparison,warning_codes "
            "FROM research_day_counts ORDER BY service_date").fetchall()
        assert days
        assert all(recommended == 0 for _, recommended, _ in days)
        assert all("comparison_requires_investigation" in warnings.split(",")
                   for _, _, warnings in days)
        by_date = {date: warnings.split(",") for date, _, warnings in days}
        if date_from == "20260714":
            assert "historical_stop_assignment_unresolved" in by_date["20260815"]
            assert "collector_method_transition" in by_date["20260816"]
            assert "historical_stop_assignment_unresolved" not in by_date["20260817"]
        caveats = dict(connection.execute("SELECT code,plain_english FROM research_caveats"))
        assert "comparison_requires_investigation" in caveats
        assert "historical_stop_assignment_unresolved" in caveats
        assert "did not replay" in caveats["origin_method_restatement"]
        assert connection.execute("SELECT COUNT(*) FROM timepoint_observations").fetchone()[0] == 3
    with zipfile.ZipFile(archive) as zipped:
        readme = zipped.read(exporter.README_MEMBER).decode()
    assert "No date cutoff certifies" in readme
    assert "WHERE service_date BETWEEN '20260715'" not in readme
    assert "not a validated comparison cohort" in readme
    assert source_hash == downloader.sha256_file(source)


def test_malformed_nested_evidence_fails_closed_and_cleans_temp_files(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = audit_fixture(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE matching_evidence SET reasons_json='not json'")
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    with pytest.raises(exporter.ResearchExportError, match="invalid reasons_json"):
        exporter.create_export(
            audit_db=source, export_root=root,
            lock_path=tmp_path / "heavy.lock", request_id="0123456789ab",
            from_value="20260830", to_value="20260831")
    assert list(root.iterdir()) == []


def test_fleet_route_pairs_are_strict_and_cannot_carry_nested_data(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = audit_fixture(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE daily_fleet_summary SET routes_json=?",
            (json.dumps([["24", {"unexpected": "nested"}]]),))
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    with pytest.raises(exporter.ResearchExportError, match="unsafe routes_json"):
        exporter.create_export(
            audit_db=source, export_root=root,
            lock_path=tmp_path / "heavy.lock", request_id="0123456789ab",
            from_value="20260830", to_value="20260831")
    assert list(root.iterdir()) == []


def test_period_and_request_paths_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = audit_fixture(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(exporter, "MIN_FREE_BYTES", 0)
    with pytest.raises(exporter.ResearchExportError, match="12 lowercase hex"):
        exporter.create_export(
            audit_db=source, export_root=root, lock_path=tmp_path / "heavy.lock",
            request_id="../bad", from_value="20260830", to_value="20260831")
    with pytest.raises(exporter.ResearchExportError, match="inside"):
        exporter.create_export(
            audit_db=source, export_root=root, lock_path=tmp_path / "heavy.lock",
            request_id="0123456789ab", from_value="20260801", to_value="20260831")


def test_downloader_rejects_corruption_unexpected_members_and_existing_output(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    result, archive = make_export(tmp_path, monkeypatch)
    corrupted = tmp_path / "corrupted.zip"
    content = bytearray(archive.read_bytes())
    content[len(content) // 2] ^= 0xFF
    corrupted.write_bytes(content)
    with pytest.raises(downloader.ResearchDownloadError):
        downloader.validate_archive(corrupted, expected=result)

    unexpected = tmp_path / "unexpected.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
            unexpected, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("surprise.txt", "no")
        target.comment = source.comment
    with pytest.raises(downloader.ResearchDownloadError, match="unexpected"):
        downloader.validate_archive(unexpected)

    final = tmp_path / "final.zip"
    final.write_bytes(b"existing")
    temporary = tmp_path / "temporary.zip"
    temporary.write_bytes(b"new")
    with pytest.raises(downloader.ResearchDownloadError, match="appeared"):
        downloader.publish_local(temporary, final)
    assert final.read_bytes() == b"existing"


def test_downloader_paths_and_remote_names_are_constrained(tmp_path: Path):
    assert downloader.validate_remote_filename(
        "collector-research-20260830-to-20260831-0123456789ab.zip")
    for value in ("../file.zip", "/tmp/file.zip", "collector-research.zip"):
        with pytest.raises(downloader.ResearchDownloadError):
            downloader.validate_remote_filename(value)

    with pytest.raises(downloader.ResearchDownloadError, match="source repository"):
        downloader.output_parent(Path(__file__).resolve().parents[2] / "private.zip")
    existing = tmp_path / "already.zip"
    existing.write_bytes(b"data")
    with pytest.raises(downloader.ResearchDownloadError, match="already exists"):
        downloader.output_parent(existing)


def test_all_exported_columns_have_plain_dictionary_entries():
    for spec in exporter.TABLE_SPECS:
        for column in spec.columns:
            entry = exporter.dictionary_entry(spec, column)
            assert entry[4]
