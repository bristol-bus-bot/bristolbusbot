import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

import enrichment_promote
from data_promotion import DataPromotionError


def raw(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def fleet_record(record_id: int, operator: str = "FBRI") -> dict:
    return {
        "id": record_id,
        "slug": f"vehicle-{record_id}",
        "fleet_code": str(36000 + record_id),
        "fleet_number": 36000 + record_id,
        "reg": f"YX26A{record_id:02d}",
        "withdrawn": False,
        "operator": {
            "id": operator,
            "slug": operator.lower(),
            "name": f"Operator {operator}",
        },
        "livery": None,
        "vehicle_type": {"name": "Test bus"},
        "garage": None,
        "special_features": None,
    }


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


def directories(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "enrichment"
    monitoring = tmp_path / "monitoring"
    (root / "incoming").mkdir(parents=True)
    monitoring.mkdir()
    return root, monitoring


def test_named_fleet_promotion_uses_fixed_contract_and_exact_health(tmp_path):
    root, monitoring = directories(tmp_path)
    old = raw([fleet_record(index) for index in range(1, 11)])
    candidate = raw([fleet_record(index) for index in range(11, 21)])
    (root / "fbribuses.json").write_bytes(old)
    (root / "incoming" / "fbribuses.json").write_bytes(candidate)
    restarts = []
    health_calls = []

    code, record = enrichment_promote.promote_named(
        "fleet",
        root=root,
        monitoring=monitoring,
        restart=lambda: restarts.append("site+bot"),
        healthy=lambda spec, path, expected, summary: health_calls.append(
            (spec.name, path, expected, summary["records"])) or True,
    )

    assert code == 0
    assert record["outcome"] == "accepted"
    assert record["comparison"]["policy"] == "fleet-bounded-count-v2"
    assert restarts == ["site+bot"]
    assert health_calls == [(
        "fleet",
        root / "fbribuses.json",
        hashlib.sha256(candidate).hexdigest(),
        10,
    )]
    assert (root / "fbribuses.json").read_bytes() == candidate
    assert (root / "fbribuses.json.previous").read_bytes() == old


def test_named_locality_promotion_is_independent_of_fleet(tmp_path):
    root, monitoring = directories(tmp_path)
    old = raw({f"B{index}": place(f"B{index}") for index in range(10)})
    candidate = raw({f"C{index}": place(f"C{index}") for index in range(10)})
    (root / "stop_localities.json").write_bytes(old)
    (root / "incoming" / "stop_localities.json").write_bytes(candidate)

    code, record = enrichment_promote.promote_named(
        "localities",
        root=root,
        monitoring=monitoring,
        restart=lambda: None,
        healthy=lambda spec, _path, _expected, summary: (
            spec.name == "localities" and summary["records"] == 10),
    )

    assert code == 0
    assert record["artifact"] == "localities"
    assert not (root / "fbribuses.json").exists()


def test_named_promotion_refuses_operator_collapse_before_writing(tmp_path):
    root, monitoring = directories(tmp_path)
    live_records = [
        *(fleet_record(index) for index in range(1, 11)),
        *(fleet_record(index, "NATX") for index in range(11, 21)),
    ]
    candidate_records = [
        *(fleet_record(index) for index in range(21, 36)),
        *(fleet_record(index, "NATX") for index in range(36, 41)),
    ]
    old = raw(live_records)
    candidate = raw(candidate_records)
    (root / "fbribuses.json").write_bytes(old)
    staged = root / "incoming" / "fbribuses.json"
    staged.write_bytes(candidate)

    with pytest.raises(DataPromotionError, match="operator NATX collapsed"):
        enrichment_promote.promote_named(
            "fleet",
            root=root,
            monitoring=monitoring,
            restart=lambda: pytest.fail("restart must not run"),
            healthy=lambda *_args: True,
        )

    assert (root / "fbribuses.json").read_bytes() == old
    assert staged.read_bytes() == candidate
    assert not (root / "fbribuses.json.previous").exists()
    assert not (monitoring / "enrichment-fleet-promotion.json").exists()


def test_command_line_has_no_arbitrary_path_or_unlisted_artifact(tmp_path):
    with pytest.raises(SystemExit) as unknown:
        enrichment_promote.main(["route-details"])
    assert unknown.value.code == 2

    with pytest.raises(SystemExit) as arbitrary_path:
        enrichment_promote.main([
            "fleet", "--root", str(tmp_path),
        ])
    assert arbitrary_path.value.code == 2


def test_health_gate_requires_exact_digest_and_count_from_consumers(
        tmp_path, monkeypatch):
    live = tmp_path / "fbribuses.json"
    payload = raw([fleet_record(1)])
    live.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    spec = enrichment_promote.SPECS["fleet"]
    monkeypatch.setattr(enrichment_promote, "_service_active", lambda: True)

    def response(url: str, maximum: int = 0) -> dict:
        del maximum
        if url == enrichment_promote.SITE_HEALTH:
            return {
                "status": "ok",
                "checks": {"fleet": {
                    "loaded": True, "sha256": digest, "records": 1,
                }},
            }
        return {
            "success": True,
            "runtime": "systemd",
            "details": {"healthData": {
                "database": {
                    "timetable": {"connected": True},
                    "appData": {"connected": True},
                },
                "application": {"enrichmentData": {"fleet": {
                    "loaded": True, "sha256": digest, "records": 1,
                }}},
            }},
        }

    monkeypatch.setattr(enrichment_promote, "_json_url", response)
    assert enrichment_promote.health_once(
        spec, live, digest, {"records": 1}) is True
    assert enrichment_promote.health_once(
        spec, live, "0" * 64, {"records": 1}) is False
