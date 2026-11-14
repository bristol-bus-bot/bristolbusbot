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
import json

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


def very_late_readings(connection: sqlite3.Connection, service_dates,
                       operator: str) -> int | None:
    """Readings 20+ minutes late for an operator's (or the network's) overall
    row, from the delay histogram the rollup keeps (route NULL). None when
    the histogram is missing, so the page falls back to three bands."""
    dates = list(service_dates)
    if not dates:
        return None
    try:
        row = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN bucket = 'late_20plus' THEN n END), 0) "
            "FROM daily_delay_histogram WHERE operator = ? AND route IS NULL "
            f"AND service_date IN ({','.join('?' * len(dates))})",
            (operator, *dates)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[1] or 0) if row[0] else None


def band_sum_reasons(connection, service_date, by_operator, columns) -> list[str]:
    """The six delay bands must add up to the readings, and their on-time band
    to the on-time count, for every operator's overall row (planning #124).
    Days with no stored bands (older rollups) are not judged."""
    try:
        bands = connection.execute(
            "SELECT operator, SUM(n), SUM(CASE WHEN bucket = 'on_time' THEN n ELSE 0 END) "
            "FROM daily_delay_histogram WHERE service_date = ? AND route IS NULL "
            "GROUP BY operator", (service_date,)).fetchall()
    except sqlite3.OperationalError:
        return []
    reasons = []
    readings_at = columns.index("readings_in_gate")
    on_time_at = columns.index("on_time")
    for operator, total, on_time in bands:
        row = by_operator.get(str(operator))
        if row is None:
            continue
        if int(total or 0) != int(row[readings_at] or 0):
            reasons.append("delay_bands_do_not_add_up_to_readings")
        if int(on_time or 0) != int(row[on_time_at] or 0):
            reasons.append("on_time_band_does_not_equal_on_time")
    return sorted(set(reasons))


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

    reasons = band_sum_reasons(connection, service_date, by_operator, columns)
    concrete = [
        by_operator[operator]
        for operator in operator_list
        if operator in by_operator
    ]
    # Some databases hold only the pooled row, with no per-operator rows to
    # check an independent total against.
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
    has_support = connection.execute("SELECT 1 FROM sqlite_master WHERE name='daily_sample_support' AND type='table'").fetchone()
    columns = {row[1] for row in connection.execute('PRAGMA table_info(daily_overall_summary)')}
    for service_date in sorted(set(service_dates)):
        reasons = []
        manual = MANUAL_EXCLUSIONS.get(service_date)
        if manual:
            reasons.append(manual)
        reasons.extend(day_consistency_reasons(connection, service_date))
        if has_support and {'readings_in_gate','on_time'}.issubset(columns):
            support = connection.execute("SELECT support_json FROM daily_sample_support WHERE service_date=? AND operator='ALL' AND scope='overall' AND scope_key=''",(service_date,)).fetchone()
            summary = connection.execute("SELECT readings_in_gate,on_time FROM daily_overall_summary WHERE service_date=? AND operator='ALL'",(service_date,)).fetchone()
            if summary and summary[0] and not support:
                reasons.append('retained_sample_support_unavailable')
            if support and summary:
                counts = json.loads(support[0])
                if (counts['readings'],counts['on_time']) != tuple(summary):
                    reasons.append('retained_sample_support_differs_from_rollup')
        if reasons:
            result[service_date] = sorted(set(reasons))
    return result
