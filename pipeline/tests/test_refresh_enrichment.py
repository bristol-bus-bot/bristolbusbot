import sys
import json
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import refresh_enrichment  # noqa: E402


def record(operator, code, registration):
    return {
        "operator": {"id": operator},
        "fleet_code": code,
        "reg": registration,
    }


def test_blurb_scope_preserves_operator_and_excludes_ambiguous_legacy_codes():
    fleet = [
        record("OPAA", "101", "AA11AAA"),
        record("OPBB", "101", "BB11BBB"),
        record("OPAA", "202", "AA22AAA"),
    ]
    observed = {
        ("OPAA", "OPAA-101"),
        ("OPBB", "OPBB-101"),
        ("OPAA", "AA22_AAA"),
    }

    payload = refresh_enrichment.scoped_blurb_payload(fleet, observed)

    assert payload["schema"] == 2
    assert payload["scoped_keys"] == ["OPAA:101", "OPAA:202", "OPBB:101"]
    assert payload["codes"] == ["202"]
    assert payload["unresolved_identities"] == 0


def test_blurb_scope_records_unknown_vehicles_without_guessing():
    payload = refresh_enrichment.scoped_blurb_payload(
        [record("OPAA", "101", "AA11AAA")],
        {("OPZZ", "OPZZ-999")},
    )

    assert payload["matched_identities"] == 0
    assert payload["unresolved_identities"] == 1
    assert payload["scoped_keys"] == []
    assert payload["codes"] == []


def test_description_distribution_never_copies_a_fleet_candidate(
        tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    live = site / "fbribuses.json"
    live.write_text(json.dumps([{"id": "live"}]), encoding="utf-8")
    candidate = tmp_path / "fbribuses.candidate.json"
    candidate.write_text(json.dumps([{"id": "candidate"}]), encoding="utf-8")
    monkeypatch.setattr(refresh_enrichment, "SITE", site)
    monkeypatch.setattr(refresh_enrichment, "FLEET", candidate)
    monkeypatch.setattr(
        refresh_enrichment,
        "BLURB_SETS",
        {
            "in-service": tmp_path / "missing-description.json",
            "depot": tmp_path / "missing-depot-description.json",
            "waiting": tmp_path / "missing-waiting-description.json",
        },
    )

    refresh_enrichment.distribute()

    assert json.loads(live.read_text(encoding="utf-8")) == [{"id": "live"}]
