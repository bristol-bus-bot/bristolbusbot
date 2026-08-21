"""Dated West of England area-wide punctuality targets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime


EP_TARGET_SOURCE = (
    "West of England Enhanced Partnership Scheme V7.02 (July 2025), "
    "Appendix 5, Table 9"
)
EP_TARGET_SOURCE_SHORT = "EP Scheme V7.02, Appendix 5, Table 9"
EP_TARGET_SOURCE_URL = (
    "https://www.westofengland-ca.gov.uk/wp-content/uploads/2025/07/"
    "West-of-England-EP-Scheme-V7.0-July-2025.pdf"
)


@dataclass(frozen=True)
class PunctualityTarget:
    financial_year: str
    starts_on: str
    ends_on: str
    target_pct: int


PUNCTUALITY_TARGETS = (
    PunctualityTarget("2022-23", "2022-04-01", "2023-03-31", 81),
    PunctualityTarget("2023-24", "2023-04-01", "2024-03-31", 82),
    PunctualityTarget("2024-25", "2024-04-01", "2025-03-31", 83),
    PunctualityTarget("2025-26", "2025-04-01", "2026-03-31", 85),
    PunctualityTarget("2026-27", "2026-04-01", "2027-03-31", 87),
)


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return date.fromisoformat(text)


def target_for_service_date(service_date: date | str) -> PunctualityTarget:
    """Return the published target, failing rather than reusing a stale year."""
    day = _date(service_date)
    for target in PUNCTUALITY_TARGETS:
        if date.fromisoformat(target.starts_on) <= day <= date.fromisoformat(
                target.ends_on):
            return target
    raise ValueError(
        f"No published WECA punctuality target is configured for {day}. "
        "Update pipeline/audit_targets.py from the current Enhanced "
        "Partnership Scheme before publishing this service date."
    )


def target_metadata(service_date: date | str) -> dict:
    target = target_for_service_date(service_date)
    return {
        "current_target_pct": target.target_pct,
        "current_target_financial_year": target.financial_year,
        "current_target_starts_on": target.starts_on,
        "current_target_ends_on": target.ends_on,
        "current_target_source": EP_TARGET_SOURCE,
        "current_target_source_short": EP_TARGET_SOURCE_SHORT,
        "current_target_source_url": EP_TARGET_SOURCE_URL,
        "punctuality_target_schedule": [
            asdict(item) for item in PUNCTUALITY_TARGETS
        ],
    }
