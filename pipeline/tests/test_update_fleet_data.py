import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import update_fleet_data as fleet_shadow


def record(record_id: int, operator: str = "FBRI", *, livery: str = "red") -> dict:
    return {
        "id": record_id,
        "slug": f"{operator.lower()}-{record_id}",
        "fleet_code": str(36000 + record_id),
        "fleet_number": 36000 + record_id,
        "reg": f"YX26A{record_id:02d}",
        "withdrawn": False,
        "operator": {
            "id": operator,
            "slug": operator.lower(),
            "name": f"Operator {operator}",
        },
        "livery": {"left": livery, "right": livery},
        "vehicle_type": {"name": "Test bus"},
        "garage": None,
        "special_features": [],
    }


class Response:
    def __init__(self, value: object, url: str):
        self.raw = json.dumps(value).encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, maximum: int):
        return self.raw[:maximum]


class Opener:
    def __init__(self, pages: dict[str, list[object]]):
        self.pages = {code: list(values) for code, values in pages.items()}
        self.requests: list[tuple[object, float]] = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        code = query["operator"][0]
        value = self.pages[code].pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url)


def write_live(path: Path, records: list[dict]) -> None:
    path.write_text(json.dumps(records), encoding="utf-8")


def run(tmp_path: Path, live_records: list[dict], pages: dict[str, list[object]],
        operators: tuple[fleet_shadow.Operator, ...]):
    live = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live, live_records)
    result = fleet_shadow.build_shadow(
        live=live,
        candidate=candidate,
        report_path=report,
        operators=operators,
        opener=Opener(pages),
        attempts=1,
        pace=0.1,
        sleep=lambda _seconds: None,
    )
    return result, candidate, report


def test_success_writes_only_candidate_and_exact_difference_report(tmp_path):
    live = [record(1), record(2)]
    changed = record(1, livery="blue")
    added = record(3)

    result, candidate, report = run(
        tmp_path,
        live,
        {"FBRI": [{"results": [changed, added], "next": None}]},
        (fleet_shadow.Operator("FBRI"),),
    )

    assert json.loads((tmp_path / "live.json").read_text()) == live
    assert json.loads(candidate.read_text()) == [changed, added]
    assert result["outcome"] == "accepted-shadow"
    assert result["promotion_attempted"] is False
    assert result["difference"]["added"] == 1
    assert result["difference"]["removed"] == 1
    assert result["difference"]["changed"] == 1
    assert result["difference"]["added_records"] == [{
        "id": 3,
        "operator": "FBRI",
        "fleet_code": "36003",
        "registration": "YX26A03",
    }]
    assert result["difference"]["removed_records"][0]["id"] == 2
    assert result["difference"]["changed_records"] == [{
        "id": 1,
        "live_operator": "FBRI",
        "candidate_operator": "FBRI",
        "fields": ["livery"],
    }]
    assert json.loads(report.read_text())["candidate_written"] is True


def test_pagination_is_bounded_to_same_operator_and_uses_honest_identity(tmp_path):
    live = [record(1), record(2)]
    second = fleet_shadow._source_url("FBRI") + "&page=2"
    opener = Opener({"FBRI": [
        {"results": [record(1)], "next": second},
        {"results": [record(2)], "next": None},
    ]})
    live_path = tmp_path / "live.json"
    write_live(live_path, live)

    result = fleet_shadow.build_shadow(
        live=live_path,
        candidate=tmp_path / "candidate.json",
        report_path=tmp_path / "report.json",
        operators=(fleet_shadow.Operator("FBRI"),),
        opener=opener,
        attempts=1,
        pace=0.1,
        sleep=lambda _seconds: None,
    )

    assert result["operators"][0]["pages"] == 2
    assert len(opener.requests) == 2
    for request, timeout in opener.requests:
        assert request.get_header("User-agent") == fleet_shadow.SOURCE_USER_AGENT
        assert timeout == 20


def test_one_source_failure_discards_everything_but_checks_other_operators(tmp_path):
    live = [record(index) for index in range(1, 11)] + [
        record(index, "NATX") for index in range(11, 21)
    ]
    live_path = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live_path, live)
    candidate.write_text("stale", encoding="utf-8")
    opener = Opener({
        "FBRI": [urllib.error.URLError("offline")],
        "NATX": [{"results": live[10:], "next": None}],
    })

    with pytest.raises(fleet_shadow.FleetShadowError,
                       match="configured operators failed"):
        fleet_shadow.build_shadow(
            live=live_path,
            candidate=candidate,
            report_path=report,
            operators=(fleet_shadow.Operator("FBRI"),
                       fleet_shadow.Operator("NATX")),
            opener=opener,
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    assert not candidate.exists()
    value = json.loads(report.read_text())
    assert value["failure_code"] == "operator_source_failure"
    assert [item["status"] for item in value["operators"]] == [
        "source-failed", "fetched"
    ]
    assert value["operators"][0]["failure_code"] == "source_failed"


def test_unexplained_empty_operator_rejects_candidate(tmp_path):
    live_path = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live_path, [record(1)])

    with pytest.raises(fleet_shadow.FleetShadowError):
        fleet_shadow.build_shadow(
            live=live_path,
            candidate=candidate,
            report_path=report,
            operators=(fleet_shadow.Operator("FBRI"),),
            opener=Opener({"FBRI": [{"results": [], "next": None}]}),
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    value = json.loads(report.read_text())
    assert value["operators"][0]["failure_code"] == "unexplained_empty"
    assert not candidate.exists()


def test_explicitly_explained_empty_operator_is_recorded(tmp_path):
    result, candidate, _report = run(
        tmp_path,
        [record(1)],
        {
            "FBRI": [{"results": [record(1)], "next": None}],
            "KEMT": [{"results": [], "next": None}],
        },
        (fleet_shadow.Operator("FBRI"),
         fleet_shadow.Operator("KEMT", "known empty baseline")),
    )

    assert candidate.exists()
    assert result["operators"][1] == {
        "code": "KEMT",
        "status": "fetched",
        "pages": 1,
        "active_records": 0,
        "empty_reason": "known empty baseline",
    }


def test_one_operator_count_collapse_is_rejected_after_fetch(tmp_path):
    live = [record(index) for index in range(1, 11)] + [
        record(index, "NATX") for index in range(11, 21)
    ]
    candidate_records = [record(index) for index in range(21, 36)] + [
        record(index, "NATX") for index in range(36, 41)
    ]
    live_path = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live_path, live)

    with pytest.raises(fleet_shadow.FleetShadowError,
                       match="operator NATX collapsed"):
        fleet_shadow.build_shadow(
            live=live_path,
            candidate=candidate,
            report_path=report,
            operators=(fleet_shadow.Operator("FBRI"),
                       fleet_shadow.Operator("NATX")),
            opener=Opener({
                "FBRI": [{"results": candidate_records[:15], "next": None}],
                "NATX": [{"results": candidate_records[15:], "next": None}],
            }),
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    assert not candidate.exists()
    assert json.loads(report.read_text())["failure_code"] == "candidate_contract"


def test_cross_host_pagination_is_a_named_source_failure(tmp_path):
    live_path = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live_path, [record(1)])

    with pytest.raises(fleet_shadow.FleetShadowError):
        fleet_shadow.build_shadow(
            live=live_path,
            candidate=candidate,
            report_path=report,
            operators=(fleet_shadow.Operator("FBRI"),),
            opener=Opener({"FBRI": [{
                "results": [record(1)],
                "next": "https://example.com/steal",
            }]}),
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    value = json.loads(report.read_text())
    assert value["operators"][0]["failure_code"] == "unsafe_source_url"


def test_exact_vehicle_ids_allow_the_explicit_vitr_to_kemt_transition(tmp_path):
    old = record(1, "VITR")
    moved = record(1, "KEMT")

    result, candidate, _report = run(
        tmp_path,
        [old],
        {
            "KEMT": [{"results": [moved], "next": None}],
            "VITR": [{"results": [], "next": None}],
        },
        (fleet_shadow.Operator("KEMT"),
         fleet_shadow.Operator("VITR", fleet_shadow.VITR_TRANSITION)),
    )

    assert candidate.exists()
    assert result["operator_transitions"] == [{
        "legacy": "VITR",
        "replacement": "KEMT",
        "status": "exact-id-transition-accepted",
        "live_legacy_records": 1,
        "matched_replacement_records": 1,
        "missing_ids": 0,
    }]


def test_vitr_to_kemt_transition_rejects_any_missing_live_vehicle_id(tmp_path):
    live_path = tmp_path / "live.json"
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"
    write_live(live_path, [record(1, "VITR"), record(2, "VITR")])

    with pytest.raises(fleet_shadow.FleetShadowError,
                       match="1 live vehicle ids are missing"):
        fleet_shadow.build_shadow(
            live=live_path,
            candidate=candidate,
            report_path=report,
            operators=(fleet_shadow.Operator("KEMT"),
                       fleet_shadow.Operator(
                           "VITR", fleet_shadow.VITR_TRANSITION)),
            opener=Opener({
                "KEMT": [{"results": [record(1, "KEMT")], "next": None}],
                "VITR": [{"results": [], "next": None}],
            }),
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    assert not candidate.exists()
    value = json.loads(report.read_text())
    assert value["failure_code"] == "operator_transition_incomplete"


def test_candidate_may_never_be_the_live_path(tmp_path):
    live = tmp_path / "live.json"
    report = tmp_path / "report.json"
    write_live(live, [record(1)])

    with pytest.raises(fleet_shadow.FleetShadowError, match="must differ"):
        fleet_shadow.build_shadow(
            live=live,
            candidate=live,
            report_path=report,
            operators=(fleet_shadow.Operator("FBRI"),),
            opener=Opener({"FBRI": []}),
            attempts=1,
            pace=0.1,
            sleep=lambda _seconds: None,
        )

    assert json.loads(live.read_text())[0]["id"] == 1
