from datetime import date
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from audit_targets import target_for_service_date, target_metadata  # noqa: E402


@pytest.mark.parametrize(
    ("service_date", "financial_year", "target_pct"),
    [
        ("20230331", "2022-23", 81),
        ("20230401", "2023-24", 82),
        ("20250331", "2024-25", 83),
        ("20250401", "2025-26", 85),
        (date(2026, 3, 31), "2025-26", 85),
        ("2026-04-01", "2026-27", 87),
        ("20270331", "2026-27", 87),
    ],
)
def test_target_changes_on_financial_year_boundary(
        service_date, financial_year, target_pct):
    target = target_for_service_date(service_date)

    assert target.financial_year == financial_year
    assert target.target_pct == target_pct


@pytest.mark.parametrize("service_date", ["20220331", "20270401"])
def test_unpublished_year_fails_instead_of_reusing_old_target(service_date):
    with pytest.raises(ValueError, match="No published WECA punctuality target"):
        target_for_service_date(service_date)


def test_export_metadata_names_the_year_source_and_full_schedule():
    metadata = target_metadata("20260822")

    assert metadata["current_target_pct"] == 87
    assert metadata["current_target_financial_year"] == "2026-27"
    assert metadata["current_target_starts_on"] == "2026-04-01"
    assert metadata["current_target_ends_on"] == "2027-03-31"
    assert "Appendix 5, Table 9" in metadata["current_target_source"]
    assert metadata["current_target_source_url"].startswith("https://")
    assert [row["target_pct"] for row in
            metadata["punctuality_target_schedule"]] == [81, 82, 83, 85, 87]
