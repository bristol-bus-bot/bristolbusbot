import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

from enrichment_contracts import (
    EnrichmentContractError,
    compare_fleet,
    compare_localities,
    validate_fleet,
    validate_localities,
)


def raw(value: object) -> bytes:
    return json.dumps(value).encode()


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
        "livery": {"name": "Test"},
        "vehicle_type": {"name": "Test bus"},
        "garage": None,
        "special_features": [],
    }


def locality(code: str, area: str = "Bristol") -> dict:
    return {
        "stop_code": code,
        "stop_name": f"Stop {code}",
        "ward_name": "Central",
        "ward_code": "E0001",
        "area": area,
        "lat": 51.45,
        "lon": -2.59,
    }


def test_fleet_validator_records_operator_coverage():
    summary = validate_fleet(raw([
        fleet_record(1),
        fleet_record(2),
        fleet_record(3, "NATX"),
    ]))

    assert summary["records"] == 3
    assert summary["active_operator_counts"] == {"FBRI": 2, "NATX": 1}
    assert summary["policy"] == "fleet-structure-v1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda records: records.append("not-an-object"), "must be an object"),
        (lambda records: records[1].update(id=records[0]["id"]), "duplicate id"),
        (lambda records: records[0]["operator"].update(id="bad id"),
         "operator id is invalid"),
        (lambda records: records[0].update(withdrawn="no"),
         "withdrawn must be boolean"),
    ],
)
def test_fleet_validator_rejects_malformed_records(change, message):
    records = [fleet_record(1), fleet_record(2)]
    change(records)
    with pytest.raises(EnrichmentContractError, match=message):
        validate_fleet(raw(records))


def test_fleet_comparator_rejects_one_operator_collapsing():
    live = validate_fleet(raw([
        *(fleet_record(index) for index in range(1, 11)),
        *(fleet_record(index, "NATX") for index in range(11, 21)),
    ]))
    candidate_records = [
        *(fleet_record(index) for index in range(21, 36)),
        *(fleet_record(index, "NATX") for index in range(36, 41)),
    ]
    candidate = validate_fleet(raw(candidate_records))

    with pytest.raises(EnrichmentContractError, match="operator NATX collapsed"):
        compare_fleet(candidate, live)


def test_fleet_comparator_records_bounded_change_and_new_operator():
    live = validate_fleet(raw([
        *(fleet_record(index) for index in range(1, 11)),
        *(fleet_record(index, "NATX") for index in range(11, 21)),
    ]))
    candidate = validate_fleet(raw([
        *(fleet_record(index) for index in range(21, 31)),
        *(fleet_record(index, "NATX") for index in range(31, 41)),
        fleet_record(41, "ABUS"),
    ]))

    result = compare_fleet(candidate, live)

    assert result["operators"]["FBRI"]["candidate"] == 10
    assert result["new_operators"] == ["ABUS"]


def test_fleet_validator_counts_records_without_public_identity():
    record = fleet_record(1)
    record.update(fleet_code="", fleet_number=None, reg="")

    summary = validate_fleet(raw([record]))

    assert summary["unidentified_records"] == 1


def test_locality_validator_records_area_coverage():
    summary = validate_localities(raw({
        "0100A": locality("0100A"),
        "0180B": locality("0180B", "North Somerset"),
    }))

    assert summary["records"] == 2
    assert summary["area_counts"] == {"Bristol": 1, "North Somerset": 1}


def test_locality_validator_normalises_legacy_bath_area_name():
    summary = validate_localities(raw({
        "0180B": locality("0180B", "Bath"),
    }))

    assert summary["area_counts"] == {"Bath and North East Somerset": 1}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda places: places["0100A"].update(stop_code="other"),
         "key does not match"),
        (lambda places: places["0100A"].update(area="Gloucestershire"),
         "outside the contract"),
        (lambda places: places["0100A"].update(lat=100),
         "latitude is invalid"),
        (lambda places: places["0100A"].update(ward_name=[]),
         "ward_name must be text or null"),
    ],
)
def test_locality_validator_rejects_malformed_records(change, message):
    places = {"0100A": locality("0100A")}
    change(places)
    with pytest.raises(EnrichmentContractError, match=message):
        validate_localities(raw(places))


def test_locality_comparator_rejects_known_area_collapse():
    live_places = {
        f"B{index}": locality(f"B{index}") for index in range(10)
    }
    live_places.update({
        f"N{index}": locality(f"N{index}", "North Somerset")
        for index in range(10)
    })
    candidate_places = copy.deepcopy(live_places)
    for index in range(5):
        candidate_places.pop(f"N{index}")
    candidate_places.update({
        f"X{index}": locality(f"X{index}") for index in range(5)
    })

    with pytest.raises(
            EnrichmentContractError, match="area North Somerset collapsed"):
        compare_localities(
            validate_localities(raw(candidate_places)),
            validate_localities(raw(live_places)),
        )


def test_locality_comparator_allows_unknowns_to_be_resolved():
    live = validate_localities(raw({
        **{f"B{index}": locality(f"B{index}") for index in range(10)},
        **{f"U{index}": locality(f"U{index}", "Unknown")
           for index in range(5)},
    }))
    candidate = validate_localities(raw({
        **{f"B{index}": locality(f"B{index}") for index in range(10)},
        **{f"U{index}": locality(f"U{index}") for index in range(5)},
    }))

    result = compare_localities(candidate, live)

    assert result["unknown"] == {"live": 5, "candidate": 0}
