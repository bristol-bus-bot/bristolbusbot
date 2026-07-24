import json
import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_geo  # noqa: E402
import audit_rollup  # noqa: E402


def test_repository_fallback_loads_canonical_site_geography(monkeypatch):
    monkeypatch.delenv("BBB_STOP_LOCALITIES", raising=False)
    monkeypatch.setattr(audit_geo, "LOCALITIES", Path("missing-pipeline-copy.json"))

    index = audit_geo.load_geo_index()

    assert len(index) >= 4_000
    assert any(row["area"] == "Bristol" for row in index.values())
    assert any(row["ward"] != "Unknown" for row in index.values())


def test_missing_geography_is_a_hard_failure(tmp_path):
    with pytest.raises(RuntimeError, match="required audit geography"):
        audit_geo.load_geo_index(tmp_path / "missing.json")


def test_invalid_or_empty_geography_is_a_hard_failure(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="required audit geography"):
        audit_geo.load_geo_index(invalid)

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty or invalid"):
        audit_geo.load_geo_index(empty)


def test_real_stop_is_rolled_up_into_area_and_ward():
    index = audit_geo.load_geo_index()
    stop_code, geography = next(
        (code, row)
        for code, row in index.items()
        if row["area"] != "Unknown" and row["ward"] != "Unknown"
    )
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """CREATE TABLE timepoint_observations (
               service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
               stop_sequence INTEGER, stop_code TEXT, scheduled_local TEXT,
               observed_delay_s INTEGER, gps_distance_m INTEGER,
               vehicle_ref TEXT
           );
           CREATE TABLE expected_trips (
               service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
               first_departure TEXT
           );"""
    )
    conn.execute(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("20260723", "FBRI", "1", "trip-1", 1, stop_code,
         "2026-07-23T08:00:00", 120, 10, "FBRI-100"),
    )
    audit_rollup.init_summary_tables(conn)

    groups = audit_rollup.rollup_geo(
        conn, "20260723", ["FBRI"], "FBRI", index
    )
    rows = {
        (geo_type, geo_key): readings
        for geo_type, geo_key, readings in conn.execute(
            """SELECT geo_type, geo_key, readings_in_gate
               FROM daily_geo_summary"""
        )
    }

    assert groups == 2
    assert rows[("area", geography["area"])] == 1
    assert rows[("ward", geography["ward"])] == 1


def test_geography_match_preflight_reports_bad_lookup_coverage():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT, operator TEXT, stop_code TEXT,
               gps_distance_m INTEGER
           )"""
    )
    conn.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?)",
        [
            ("20260723", "FBRI", "KNOWN", 10),
            ("20260723", "FBRI", "MISSING", 10),
            ("20260723", "SCGL", "KNOWN", 10),
            ("20260723", "OTHER", "MISSING", 10),
            ("20260723", "FBRI", "MISSING", 151),
        ],
    )

    stats = audit_rollup.geography_match_stats(
        conn, "20260723", ["FBRI", "SCGL"], {"KNOWN": {"area": "Bristol"}}
    )

    assert stats == {"eligible": 3, "matched": 2, "pct": 66.7}
