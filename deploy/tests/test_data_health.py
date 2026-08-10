from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from deploy import data_health


def test_default_timetable_path_matches_the_durable_production_layout():
    assert data_health.DEFAULT_TIMETABLE_DB == Path(
        "/var/lib/bristolbusbot/pipeline/timetable.db")


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def databases(tmp_path: Path, *, vehicle_ref: str = "FBRI-100") -> tuple[Path, Path, Path]:
    live = tmp_path / "live.db"
    with sqlite3.connect(live) as connection:
        connection.execute(
            "CREATE TABLE vehicles (operator_ref TEXT, vehicle_ref TEXT, updated_at TEXT)")
        connection.execute(
            "INSERT INTO vehicles VALUES (?,?,?)",
            ("FBRI", vehicle_ref, datetime.now(timezone.utc).isoformat()))

    audit = tmp_path / "audit.db"
    with sqlite3.connect(audit) as connection:
        connection.execute(
            "CREATE TABLE timepoint_observations "
            "(operator TEXT, vehicle_ref TEXT, service_date TEXT)")

    timetable = tmp_path / "timetable.db"
    with sqlite3.connect(timetable) as connection:
        connection.execute(
            "CREATE TABLE stops (stop_code TEXT, stop_lat REAL, stop_lon REAL)")
        connection.execute(
            "INSERT INTO stops VALUES ('0100BRA10000', 51.45, -2.60)")
    return live, audit, timetable


def vehicle(code: int, *, livery: bool = True, operator: str = "FBRI") -> dict:
    return {
        "fleet_code": str(code),
        "reg": f"WX26{code:03d}",
        "operator": {"id": operator},
        "vehicle_type": {"name": "Test Bus"},
        "livery": ({"left": "linear-gradient(red,blue)",
                    "right": "linear-gradient(blue,red)"} if livery else None),
        "withdrawn": False,
    }


def paths(tmp_path: Path, fleet: list[dict]) -> dict:
    live, audit, timetable = databases(tmp_path)
    fleet_path = tmp_path / "fbribuses.json"
    localities = tmp_path / "stop_localities.json"
    write_json(fleet_path, fleet)
    write_json(localities, {"0100BRA10000": {"locality": "Bristol"}})
    descriptions = {}
    for name in ("in_service", "waiting", "depot"):
        path = tmp_path / f"{name}.json"
        write_json(path, {str(item["fleet_code"]): "A bus." for item in fleet})
        descriptions[name] = path
    return {
        "output": tmp_path / "data-health.json",
        "live_db": live,
        "audit_db": audit,
        "timetable_db": timetable,
        "fleet_path": fleet_path,
        "localities_path": localities,
        "description_paths": descriptions,
        "model_context_path": tmp_path / "model-context.json",
    }


def test_clean_report_is_read_only_and_model_context_is_deferred(tmp_path):
    inputs = paths(tmp_path, [vehicle(100)])

    report = data_health.build_report(**inputs)

    assert report["status"] == "clean"
    assert report["mode"] == "report_only"
    assert report["summary"]["observed_identities"] == 1
    assert report["summary"]["matched_fleet_identities"] == 1
    assert report["findings"] == []
    assert report["model_context"]["status"] == "not_configured"
    assert not inputs["output"].exists()


def test_missing_enrichment_is_bounded_and_warns(tmp_path):
    inputs = paths(tmp_path, [vehicle(100, livery=False)])
    write_json(inputs["description_paths"]["waiting"], {})
    write_json(inputs["localities_path"], {})

    report = data_health.build_report(**inputs)

    codes = {finding["code"] for finding in report["findings"]}
    assert report["status"] == "warning"
    assert "observed_vehicle_missing_livery" in codes
    assert "observed_vehicle_missing_waiting_blurb" in codes
    assert "timetable_stop_missing_locality" in codes
    assert all(len(finding.get("examples", [])) <= 12
               for finding in report["findings"])


def test_previous_operator_count_collapse_is_detected(tmp_path):
    fleet = [vehicle(code) for code in range(100, 120)]
    inputs = paths(tmp_path, fleet)
    write_json(inputs["output"], {
        "schema_version": data_health.SCHEMA_VERSION,
        "fleet": {"active_by_operator": {"FBRI": 100}},
    })

    report = data_health.build_report(**inputs)

    collapse = report["fleet"]["operator_collapses"][0]
    assert collapse == {
        "operator": "FBRI", "previous": 100, "current": 20, "ratio": 0.2}
    assert "operator_count_collapse" in {
        finding["code"] for finding in report["findings"]}


def test_explicit_vitr_to_kemt_transition_is_not_a_false_collapse(tmp_path):
    fleet = [
        *(vehicle(code, operator="KEMT") for code in range(100, 114)),
        *(vehicle(code) for code in range(200, 220)),
    ]
    inputs = paths(tmp_path, fleet)
    write_json(inputs["output"], {
        "schema_version": data_health.SCHEMA_VERSION,
        "fleet": {"active_by_operator": {
            "FBRI": 20, "KEMT": 3, "VITR": 11,
        }},
    })

    report = data_health.build_report(**inputs)

    assert report["fleet"]["operator_collapses"] == []
    assert report["fleet"]["operator_transitions"] == [{
        "legacy": "VITR",
        "replacement": "KEMT",
        "status": "explicit-transition-baseline",
        "previous_legacy": 11,
        "previous_replacement": 3,
        "current_replacement": 14,
    }]


def test_explicit_operator_transition_still_detects_replacement_collapse(
        tmp_path):
    fleet = [
        *(vehicle(code, operator="KEMT") for code in range(100, 103)),
        *(vehicle(code) for code in range(200, 220)),
    ]
    inputs = paths(tmp_path, fleet)
    write_json(inputs["output"], {
        "schema_version": data_health.SCHEMA_VERSION,
        "fleet": {"active_by_operator": {
            "FBRI": 20, "KEMT": 3, "VITR": 11,
        }},
    })

    report = data_health.build_report(**inputs)

    assert report["fleet"]["operator_collapses"] == [{
        "operator": "KEMT", "previous": 14, "current": 3, "ratio": 0.214,
    }]


def test_operator_scope_prevents_cross_operator_fleet_match(tmp_path):
    inputs = paths(tmp_path, [vehicle(100)])
    with sqlite3.connect(inputs["live_db"]) as connection:
        connection.execute("UPDATE vehicles SET operator_ref='ABUS'")

    report = data_health.build_report(**inputs)

    assert report["summary"]["missing_fleet"] == 1
    assert report["enrichment"]["missing_fleet_examples"] == ["ABUS:FBRI-100"]


def test_shared_code_requires_operator_scoped_description(tmp_path):
    inputs = paths(tmp_path, [vehicle(100), vehicle(100, operator="ABUS")])

    report = data_health.build_report(**inputs)

    assert report["summary"]["missing_blurbs"] == {
        "in_service": 1, "waiting": 1, "depot": 1}


def test_out_of_area_timetable_stops_do_not_need_weca_locality_data(tmp_path):
    inputs = paths(tmp_path, [vehicle(100)])
    with sqlite3.connect(inputs["timetable_db"]) as connection:
        connection.execute(
            "INSERT INTO stops VALUES ('outside-weca', 52.50, -1.90)")

    report = data_health.build_report(**inputs)

    assert report["status"] == "clean"
    assert report["stops"]["timetable"] == 1
    assert report["stops"]["missing"] == 0
