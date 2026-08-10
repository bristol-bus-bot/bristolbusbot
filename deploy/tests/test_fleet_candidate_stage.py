import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

from fleet_candidate_stage import FleetStageError, stage_candidate


NOW = datetime(2026, 8, 10, 20, tzinfo=timezone.utc)


def record(record_id: int, operator: str = "FBRI") -> dict:
    return {
        "id": record_id,
        "slug": f"vehicle-{record_id}",
        "fleet_code": str(record_id),
        "fleet_number": record_id,
        "reg": f"TEST{record_id}",
        "withdrawn": False,
        "operator": {"id": operator, "slug": operator.lower(),
                     "name": operator},
        "livery": None,
        "vehicle_type": None,
        "garage": None,
        "special_features": [],
    }


def raw(records: list[dict]) -> bytes:
    return (json.dumps(records) + "\n").encode()


def write_run(tmp_path: Path, live_records: list[dict],
              candidate_records: list[dict]):
    live = tmp_path / "live.json"
    candidate = tmp_path / "shadow.json"
    report = tmp_path / "report.json"
    incoming = tmp_path / "incoming" / "fleet.json"
    incoming.parent.mkdir()
    live_raw = raw(live_records)
    candidate_raw = raw(candidate_records)
    live.write_bytes(live_raw)
    candidate.write_bytes(candidate_raw)
    report.write_text(json.dumps({
        "schema": 1,
        "mode": "shadow-only",
        "outcome": "accepted-shadow",
        "candidate_written": True,
        "promotion_attempted": False,
        "finished_at": (NOW - timedelta(minutes=10)).isoformat(),
        "candidate": {"sha256": hashlib.sha256(candidate_raw).hexdigest()},
        "live": {"sha256": hashlib.sha256(live_raw).hexdigest()},
    }))
    return live, candidate, report, incoming


def stage(paths, **kwargs):
    live, candidate, report, incoming = paths
    result = stage_candidate(
        shadow_candidate=candidate,
        shadow_report=report,
        live_fleet=live,
        promotion_candidate=incoming,
        now=NOW,
        **kwargs,
    )
    return result, incoming


def test_stages_only_a_fresh_matching_candidate(tmp_path):
    paths = write_run(
        tmp_path,
        [record(index) for index in range(1, 11)],
        [record(index) for index in range(1, 12)],
    )

    result, incoming = stage(paths)

    assert result["status"] == "staged"
    assert result["candidate"]["records"] == 11
    assert incoming.read_bytes() == paths[1].read_bytes()


def test_rejects_stale_report(tmp_path):
    paths = write_run(tmp_path, [record(1)], [record(1)])
    report = json.loads(paths[2].read_text())
    report["finished_at"] = (NOW - timedelta(hours=3)).isoformat()
    paths[2].write_text(json.dumps(report))

    with pytest.raises(FleetStageError, match="stale"):
        stage(paths)

    assert not paths[3].exists()


def test_rejects_candidate_changed_after_report(tmp_path):
    paths = write_run(tmp_path, [record(1)], [record(1)])
    paths[1].write_bytes(raw([record(1), record(2)]))

    with pytest.raises(FleetStageError, match="candidate no longer matches"):
        stage(paths)


def test_rejects_live_changed_after_report(tmp_path):
    paths = write_run(tmp_path, [record(1)], [record(1)])
    paths[0].write_bytes(raw([record(1), record(2)]))

    with pytest.raises(FleetStageError, match="live fleet changed"):
        stage(paths)


def test_rejects_incomplete_operator_transition(tmp_path):
    paths = write_run(
        tmp_path,
        [record(1, "VITR"), record(2, "VITR"),
         *(record(index) for index in range(3, 11))],
        [record(1, "KEMT"), *(record(index) for index in range(3, 11))],
    )

    with pytest.raises(Exception, match="transition VITR->KEMT is incomplete"):
        stage(paths)


def test_rejects_symlink_candidate(tmp_path):
    paths = write_run(tmp_path, [record(1)], [record(1)])
    target = tmp_path / "real-candidate.json"
    paths[1].replace(target)
    try:
        paths[1].symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(FleetStageError, match="unsafe"):
        stage(paths)
