import sys
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
