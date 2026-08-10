import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))

from locality_candidate_stage import LocalityStageError, stage_candidate


NOW = datetime(2026, 8, 10, 20, tzinfo=timezone.utc)


def place(code: str) -> dict:
    return {
        "stop_code": code,
        "stop_name": f"Stop {code}",
        "ward_name": "Central",
        "ward_code": "E0001",
        "area": "Bristol",
        "lat": 51.45,
        "lon": -2.59,
    }


def raw(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def write_run(tmp_path: Path, candidate_codes: list[str]):
    live_codes = [f"S{index}" for index in range(10)]
    live = tmp_path / "live.json"
    candidate = tmp_path / "shadow.json"
    report = tmp_path / "report.json"
    timetable = tmp_path / "timetable.db"
    incoming = tmp_path / "incoming" / "localities.json"
    incoming.parent.mkdir()
    live_raw = raw({code: place(code) for code in live_codes})
    candidate_raw = raw({code: place(code) for code in candidate_codes})
    live.write_bytes(live_raw)
    candidate.write_bytes(candidate_raw)
    connection = sqlite3.connect(timetable)
    connection.execute(
        "CREATE TABLE stops (stop_code TEXT, stop_name TEXT, "
        "stop_lat REAL, stop_lon REAL)")
    connection.executemany(
        "INSERT INTO stops VALUES (?, ?, ?, ?)",
        [(code, f"Stop {code}", 51.45, -2.59) for code in candidate_codes],
    )
    connection.commit()
    connection.close()
    report.write_text(json.dumps({
        "schema": 1,
        "mode": "shadow-only",
        "outcome": "accepted-shadow",
        "candidate_written": True,
        "promotion_attempted": False,
        "finished_at": (NOW - timedelta(minutes=10)).isoformat(),
        "candidate": {"sha256": hashlib.sha256(candidate_raw).hexdigest()},
        "live": {"sha256": hashlib.sha256(live_raw).hexdigest()},
        "coverage": {
            "timetable_stops": len(candidate_codes),
            "candidate_stops": len(candidate_codes),
            "missing": 0,
            "extra": 0,
        },
        "boundary": {
            "mode": "fetched",
            "edition": "December 2025",
            "source": (
                "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/"
                "rest/services/WD_DEC_2025_UK_BSC/FeatureServer/0/query"),
            "sha256": "a" * 64,
            "feature_count": 130,
            "authority_codes": [
                "E06000022", "E06000023", "E06000024", "E06000025"],
        },
    }))
    return live, candidate, report, timetable, incoming


def run_stage(paths):
    live, candidate, report, timetable, incoming = paths
    result = stage_candidate(
        shadow_candidate=candidate,
        shadow_report=report,
        live_localities=live,
        live_timetable=timetable,
        promotion_candidate=incoming,
        now=NOW,
    )
    return result, incoming


def test_stages_fresh_candidate_with_exact_live_timetable_coverage(tmp_path):
    paths = write_run(tmp_path, [f"N{index}" for index in range(10)])

    result, incoming = run_stage(paths)

    assert result["status"] == "staged"
    assert result["candidate"]["records"] == 10
    assert incoming.read_bytes() == paths[1].read_bytes()


def test_rejects_candidate_that_no_longer_matches_timetable(tmp_path):
    paths = write_run(tmp_path, [f"N{index}" for index in range(10)])
    connection = sqlite3.connect(paths[3])
    connection.execute(
        "INSERT INTO stops VALUES (?, ?, ?, ?)",
        ("EXTRA", "Extra", 51.45, -2.59),
    )
    connection.commit()
    connection.close()

    with pytest.raises(LocalityStageError, match="live timetable"):
        run_stage(paths)

    assert not paths[4].exists()


def test_rejects_unapproved_boundary_provenance(tmp_path):
    paths = write_run(tmp_path, [f"N{index}" for index in range(10)])
    report = json.loads(paths[2].read_text())
    report["boundary"]["edition"] = "moving latest"
    paths[2].write_text(json.dumps(report))

    with pytest.raises(LocalityStageError, match="not approved"):
        run_stage(paths)


def test_reused_live_mode_can_only_stage_identical_bytes(tmp_path):
    paths = write_run(tmp_path, [f"N{index}" for index in range(10)])
    report = json.loads(paths[2].read_text())
    report["boundary"] = {"mode": "reused-live"}
    paths[2].write_text(json.dumps(report))

    with pytest.raises(LocalityStageError, match="differs from live"):
        run_stage(paths)
