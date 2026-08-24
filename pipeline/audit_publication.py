"""Fail-closed checks shared by public audit products.

The collector cutover left 1 July 2026 with a complete inherited summary but
only a partial set of retained raw rows.  A later method backfill recalculated
that day from the partial rows.  Keep the known incident excluded until an
explicit, evidence-backed restoration removes it from this list, and detect
the pooled/operator contradiction that the incident exposed.
"""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3

from audit_operators import NETWORK_LABEL, SHOW_OPERATORS


MANUAL_EXCLUSIONS = {
    "20260701": "partial_raw_history_after_collector_cutover",
}

ADDITIVE_OVERALL_FIELDS = (
    "readings_in_gate",
    "readings_total",
    "on_time",
    "early",
    "late",
    "excluded_distance",
)


def day_consistency_reasons(
    connection: sqlite3.Connection,
    service_date: str,
    operators: Iterable[str] = SHOW_OPERATORS,
) -> list[str]:
    """Return public-summary contradictions for one service day."""
    operator_list = tuple(operators)
    rows = connection.execute(
        "SELECT * FROM daily_overall_summary WHERE service_date = ?",
        (service_date,),
    ).fetchall()
    if not rows:
        return ["daily_overall_summary_missing"]

    columns = [item[0] for item in connection.execute(
        "SELECT name FROM pragma_table_info('daily_overall_summary')"
    )]
    by_operator = {
        str(row[columns.index("operator")]): row for row in rows
    }
    network = by_operator.get(NETWORK_LABEL)
    if network is None:
        return ["pooled_summary_missing"]

    reasons = []
    concrete = [
        by_operator[operator]
        for operator in operator_list
        if operator in by_operator
    ]
    # Legacy/test databases can contain only the pooled row.  They predate the
    # operator split, so there is no independent total to check.
    if not concrete:
        return []
    for field in ADDITIVE_OVERALL_FIELDS:
        index = columns.index(field)
        pooled_value = int(network[index] or 0)
        operator_value = sum(int(row[index] or 0) for row in concrete)
        if pooled_value != operator_value:
            reasons.append(f"pooled_{field}_does_not_equal_operator_sum")
    return reasons


def publication_exclusions(
    connection: sqlite3.Connection,
    service_dates: Iterable[str],
) -> dict[str, list[str]]:
    """Return known and detected reasons a day must not be published."""
    result: dict[str, list[str]] = {}
    for service_date in sorted(set(service_dates)):
        reasons = []
        manual = MANUAL_EXCLUSIONS.get(service_date)
        if manual:
            reasons.append(manual)
        reasons.extend(day_consistency_reasons(connection, service_date))
        if reasons:
            result[service_date] = sorted(set(reasons))
    return result
