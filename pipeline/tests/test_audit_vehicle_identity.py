import json
import sqlite3
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_vehicle_identity as identity_audit  # noqa: E402


def record(operator, code, registration, *, withdrawn=False, model="Model"):
    return {
        "operator": {"id": operator},
        "fleet_code": code,
        "reg": registration,
        "withdrawn": withdrawn,
        "vehicle_type": {"name": model},
        "livery": {"name": f"{operator} livery", "left": "#123456"},
    }


def databases(tmp_path):
    live = tmp_path / "live.db"
    connection = sqlite3.connect(live)
    connection.execute(
        "CREATE TABLE vehicles (operator_ref TEXT, vehicle_ref TEXT)")
    connection.executemany(
        "INSERT INTO vehicles VALUES (?, ?)",
        [("OPAA", "OPAA-101"), ("OPBB", "OPBB-101"),
         ("OPAA", "AA11_AAA"), ("OPCC", "OPCC-999")],
    )
    connection.commit()
    connection.close()

    audit = tmp_path / "audit.db"
    connection = sqlite3.connect(audit)
    connection.execute(
        "CREATE TABLE timepoint_observations "
        "(service_date TEXT, operator TEXT, vehicle_ref TEXT)")
    connection.executemany(
        "INSERT INTO timepoint_observations VALUES (date('now'), ?, ?)",
        [("OPAA", "OPAA-101"), ("OPBB", "OPBB-101")],
    )
    connection.commit()
    connection.close()
    return live, audit


def test_audit_proves_last_record_wins_cross_operator_failure(tmp_path):
    fleet = [
        record("OPAA", "101", "AA11AAA", model="Correct A"),
        record("OPBB", "101", "BB11BBB", model="Correct B"),
    ]
    live, audit = databases(tmp_path)
    observed = identity_audit.observed_identities(live, audit)
    report = identity_audit.build_report(
        fleet, observed,
        {"bus-descriptions.json": {"101"}},
    )

    assert report["status"] == "issues_found"
    assert report["fleet"]["shared_active_fleet_code_groups"] == 1
    assert report["observed"]["wrong_legacy_fleet_matches"] == 1
    assert report["observed"]["ambiguous_description_identities"] == 2
    assert {
        (item["operator"], item["legacy_operator"])
        for item in report["examples"]["wrong_legacy_fleet_matches"]
    } == {("OPAA", "OPBB")}


def test_registration_match_is_operator_scoped_and_precedes_code():
    fleet = [
        record("OPAA", "101", "AA11AAA"),
        record("OPBB", "101", "BB11BBB"),
    ]
    index = identity_audit.indexes(fleet)

    assert identity_audit.safe_match(index, "OPAA", "AA11_AAA") is fleet[0]
    assert identity_audit.safe_match(index, "OPBB", "BB11-BBB") is fleet[1]
    assert identity_audit.safe_match(index, "OPAA", "BB11-BBB") is None


def test_unambiguous_bare_code_fallback_remains_compatible():
    fleet = [record("OPAA", "202", "AA22AAA")]
    index = identity_audit.indexes(fleet)

    assert identity_audit.safe_match(index, "", "UNKNOWN-202") is fleet[0]
    assert identity_audit.safe_match(index, "OTHER", "OTHER-202") is None


def test_same_operator_reused_code_fails_closed_unless_registration_identifies_it():
    fleet = [
        record("OPAA", "303", "AA30AAA"),
        record("OPAA", "303", "AA30BBB"),
    ]
    index = identity_audit.indexes(fleet)

    assert identity_audit.safe_match(index, "OPAA", "OPAA-303") is None
    assert identity_audit.safe_match(index, "OPAA", "AA30AAA") is fleet[0]
    assert identity_audit.safe_match(index, "OPAA", "AA30BBB") is fleet[1]


def test_registration_lookup_is_scoped_when_source_repeats_a_registration():
    fleet = [
        record("OPAA", "401", "ZZ40ZZZ"),
        record("OPBB", "402", "ZZ40ZZZ"),
    ]
    index = identity_audit.indexes(fleet)

    assert identity_audit.safe_match(index, "OPAA", "ZZ40ZZZ") is fleet[0]
    assert identity_audit.safe_match(index, "OPBB", "ZZ40ZZZ") is fleet[1]


def test_old_audit_rows_are_outside_the_observation_window(tmp_path):
    live, audit = databases(tmp_path)
    connection = sqlite3.connect(audit)
    connection.execute(
        "INSERT INTO timepoint_observations VALUES ('2020-01-01', 'OLD', 'OLD-1')")
    connection.commit()
    connection.close()

    observed = identity_audit.observed_identities(live, audit, observed_days=7)

    assert ("OLD", "OLD-1") not in observed


def test_loaders_fail_closed_without_printing_description_text(tmp_path):
    fleet_path = tmp_path / "fleet.json"
    fleet_path.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty or invalid"):
        identity_audit.load_fleet(fleet_path)

    descriptions = tmp_path / "descriptions.json"
    descriptions.write_text(json.dumps(["not", "an", "object"]),
                            encoding="utf-8")
    with pytest.raises(RuntimeError, match="not an object"):
        identity_audit.load_description_keys([descriptions])
