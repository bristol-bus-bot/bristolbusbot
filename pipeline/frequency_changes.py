#!/usr/bin/env python3
"""Compare like-for-like registered weekday journey frequencies.

This command reads the audit's durable ``expected_trips`` snapshots. It does
not use vehicle observations and it says nothing about whether a scheduled
journey actually ran.

Comparisons fail closed unless every expected row carries the registered route
ID and direction. Whole Monday-Sunday blocks are required, standard England
and Wales bank holidays are excluded, and each weekday must have a repeated
network pattern. A route whose own count varies inside either period is
withheld rather than turned into a convenient average.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterable

from dateutil.relativedelta import relativedelta


HERE = Path(__file__).resolve().parent
AUDIT_DB = Path(os.getenv("BBB_AUDIT_DB", HERE / "audit.db"))
DATE_FORMAT = "%Y%m%d"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
DEFAULT_HORIZONS = (1, 3, 6, 12)
DEFAULT_WEEKS = 4
MIN_WEEKS = 2
SCOPE_NOTE = (
    "Registered timetable journeys only. A change does not explain why the "
    "timetable changed or whether every scheduled journey ran."
)
SEASON_NOTE = (
    "School-term and other seasonal context is not reliably named in BODS. "
    "Only describe a change as permanent after checking the two periods' "
    "local calendar context."
)


@dataclass(frozen=True, order=True)
class RouteKey:
    operator: str
    route_id: str
    direction: int


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    context: str = "not supplied"

    @property
    def weeks(self) -> int:
        return ((self.end - self.start).days + 1) // 7

    @property
    def weekdays(self) -> list[date]:
        return [
            self.start + timedelta(days=offset)
            for offset in range((self.end - self.start).days + 1)
            if (self.start + timedelta(days=offset)).weekday() < 5
        ]


@dataclass
class PreparedPeriod:
    period: Period
    usable_dates: list[date]
    excluded_dates: list[dict]
    weekly_counts: dict[RouteKey, int]
    labels: dict[RouteKey, list[str]]
    unstable_routes: dict[RouteKey, str]


class ComparisonUnavailable(RuntimeError):
    """The requested dates cannot support a defensible comparison."""


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, DATE_FORMAT).date()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"expected a date in YYYYMMDD form, got {value!r}") from exc


def compact(day: date) -> str:
    return day.strftime(DATE_FORMAT)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (Meeus/Jones/Butcher), used for the two bank holidays."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def substitute_christmas_days(year: int) -> tuple[date, date]:
    christmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    if christmas.weekday() == 5:  # Saturday
        return date(year, 12, 27), date(year, 12, 28)
    if christmas.weekday() == 6:  # Sunday
        return date(year, 12, 27), date(year, 12, 26)
    if boxing.weekday() == 5:  # Christmas Friday, Boxing Day Saturday
        return christmas, date(year, 12, 28)
    return christmas, boxing


def england_wales_bank_holidays(year: int) -> dict[date, str]:
    """Return normal recurring holidays; pass one-offs with --exclude-date."""
    new_year = date(year, 1, 1)
    if new_year.weekday() == 5:
        new_year = date(year, 1, 3)
    elif new_year.weekday() == 6:
        new_year = date(year, 1, 2)
    easter = easter_sunday(year)
    christmas, boxing = substitute_christmas_days(year)
    return {
        new_year: "New Year's Day bank holiday",
        easter - timedelta(days=2): "Good Friday bank holiday",
        easter + timedelta(days=1): "Easter Monday bank holiday",
        nth_weekday(year, 5, 0, 1): "Early May bank holiday",
        last_weekday(year, 5, 0): "Spring bank holiday",
        last_weekday(year, 8, 0): "Summer bank holiday",
        christmas: "Christmas Day bank holiday",
        boxing: "Boxing Day bank holiday",
    }


def validate_period(period: Period, label: str) -> None:
    if period.end < period.start:
        raise ValueError(f"{label} period ends before it starts")
    days = (period.end - period.start).days + 1
    if period.start.weekday() != 0 or period.end.weekday() != 6 or days % 7:
        raise ValueError(
            f"{label} period must contain complete Monday-Sunday weeks")
    if period.weeks < MIN_WEEKS:
        raise ValueError(
            f"{label} period must contain at least {MIN_WEEKS} complete weeks")


def sunday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 6) % 7)


def period_ending(end: date, weeks: int, context: str = "not supplied") -> Period:
    end = sunday_on_or_before(end)
    return Period(end - timedelta(days=weeks * 7 - 1), end, context)


def parse_exclusion(value: str) -> tuple[date, str]:
    raw_date, separator, reason = value.partition("=")
    if not separator or not reason.strip():
        raise argparse.ArgumentTypeError(
            "--exclude-date must be YYYYMMDD=reason")
    return parse_date(raw_date.strip()), reason.strip()


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    return sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)


def validate_database(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='expected_trips'"
    ).fetchone()
    if table is None:
        raise RuntimeError("audit database has no expected_trips table")
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(expected_trips)")
    }
    required = {"service_date", "operator", "route", "route_id", "direction"}
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(
            "expected_trips lacks required route-identity columns: "
            + ", ".join(missing))


def trustworthy_history(connection: sqlite3.Connection) -> dict:
    rows = connection.execute("""
        SELECT service_date, COUNT(*) AS total,
               SUM(CASE WHEN route_id IS NOT NULL AND TRIM(route_id) != ''
                             AND direction IS NOT NULL
                        THEN 1 ELSE 0 END) AS identified
          FROM expected_trips
         GROUP BY service_date
         ORDER BY service_date
    """).fetchall()
    complete = [row[0] for row in rows if row[1] > 0 and row[1] == row[2]]
    return {
        "first_snapshot": rows[0][0] if rows else None,
        "last_snapshot": rows[-1][0] if rows else None,
        "snapshot_days": len(rows),
        "first_route_identity_date": complete[0] if complete else None,
        "last_route_identity_date": complete[-1] if complete else None,
        "route_identity_days": len(complete),
    }


def _date_quality(
        connection: sqlite3.Connection, dates: Iterable[date]) -> dict[date, tuple[int, int]]:
    wanted = list(dates)
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    rows = connection.execute(f"""
        SELECT service_date, COUNT(*) AS total,
               SUM(CASE WHEN route_id IS NOT NULL AND TRIM(route_id) != ''
                             AND direction IS NOT NULL
                        THEN 1 ELSE 0 END) AS identified
          FROM expected_trips
         WHERE service_date IN ({placeholders})
         GROUP BY service_date
    """, [compact(day) for day in wanted]).fetchall()
    return {parse_date(day): (total, identified) for day, total, identified in rows}


def _scheduled_counts(
        connection: sqlite3.Connection,
        period: Period,
) -> tuple[dict[date, int], dict[date, dict[RouteKey, int]],
           dict[RouteKey, set[str]]]:
    rows = connection.execute("""
        SELECT service_date, operator, route_id, route, direction, COUNT(*)
          FROM expected_trips
         WHERE service_date BETWEEN ? AND ?
         GROUP BY service_date, operator, route_id, route, direction
         ORDER BY service_date, operator, route_id, direction
    """, (compact(period.start), compact(period.end))).fetchall()
    totals: dict[date, int] = defaultdict(int)
    by_date: dict[date, dict[RouteKey, int]] = defaultdict(lambda: defaultdict(int))
    labels: dict[RouteKey, set[str]] = defaultdict(set)
    for raw_day, operator, route_id, route, direction, count in rows:
        day = parse_date(raw_day)
        key = RouteKey(str(operator), str(route_id), int(direction))
        totals[day] += int(count)
        by_date[day][key] += int(count)
        if route is not None and str(route).strip():
            labels[key].add(str(route).strip())
    return dict(totals), {day: dict(value) for day, value in by_date.items()}, labels


def prepare_period(
        connection: sqlite3.Connection,
        period: Period,
        manual_exclusions: dict[date, str] | None = None,
) -> PreparedPeriod:
    validate_period(period, "comparison")
    manual_exclusions = manual_exclusions or {}
    weekdays = period.weekdays
    excluded: dict[date, str] = {}
    for year in range(period.start.year, period.end.year + 1):
        for day, reason in england_wales_bank_holidays(year).items():
            if period.start <= day <= period.end and day.weekday() < 5:
                excluded[day] = reason
    for day, reason in manual_exclusions.items():
        if period.start <= day <= period.end and day.weekday() < 5:
            excluded[day] = reason

    required = [day for day in weekdays if day not in excluded]
    quality = _date_quality(connection, required)
    missing = [compact(day) for day in required if day not in quality]
    legacy = [
        compact(day) for day in required
        if day in quality and quality[day][0] != quality[day][1]
    ]
    if missing or legacy:
        reasons = []
        if missing:
            reasons.append("missing snapshots: " + ", ".join(missing))
        if legacy:
            reasons.append(
                "snapshots without complete route ID and direction: "
                + ", ".join(legacy))
        raise ComparisonUnavailable("; ".join(reasons))
    totals, by_date, labels = _scheduled_counts(connection, period)
    usable: list[date] = []
    for weekday in range(5):
        candidates = [
            day for day in weekdays
            if day.weekday() == weekday and day not in excluded
        ]
        pattern = Counter(totals[day] for day in candidates)
        if not pattern:
            raise ComparisonUnavailable(
                f"no usable {WEEKDAYS[weekday]} snapshots after exclusions")
        ranked = pattern.most_common()
        top_count, repetitions = ranked[0]
        tied = len(ranked) > 1 and ranked[1][1] == repetitions
        if repetitions < 2 or tied:
            detail = ", ".join(
                f"{compact(day)}={totals[day]}" for day in candidates)
            raise ComparisonUnavailable(
                f"no repeated ordinary {WEEKDAYS[weekday]} network pattern: {detail}")
        for day in candidates:
            if totals[day] == top_count:
                usable.append(day)
            else:
                excluded[day] = (
                    f"network schedule differs from the repeated "
                    f"{WEEKDAYS[weekday]} pattern "
                    f"({totals[day]} versus {top_count} trips)")

    usable.sort()
    weekly_counts: dict[RouteKey, int] = {}
    unstable: dict[RouteKey, str] = {}
    all_keys = set().union(*(set(by_date[day]) for day in usable))
    for key in sorted(all_keys):
        weekly = 0
        variations = []
        for weekday in range(5):
            same_day = [day for day in usable if day.weekday() == weekday]
            counts = [by_date[day].get(key, 0) for day in same_day]
            distinct = sorted(set(counts))
            if len(distinct) != 1:
                variations.append(
                    f"{WEEKDAYS[weekday]} counts "
                    + "/".join(str(value) for value in distinct))
            else:
                weekly += distinct[0]
        if variations:
            unstable[key] = "; ".join(variations)
        else:
            weekly_counts[key] = weekly

    return PreparedPeriod(
        period=period,
        usable_dates=usable,
        excluded_dates=[
            {"date": compact(day), "reason": excluded[day]}
            for day in sorted(excluded)
        ],
        weekly_counts=weekly_counts,
        labels={key: sorted(value) for key, value in labels.items()},
        unstable_routes=unstable,
    )


def period_payload(prepared: PreparedPeriod) -> dict:
    return {
        "start": compact(prepared.period.start),
        "end": compact(prepared.period.end),
        "weeks": prepared.period.weeks,
        "context": prepared.period.context,
        "usable_days": len(prepared.usable_dates),
        "usable_dates": [compact(day) for day in prepared.usable_dates],
        "excluded_dates": prepared.excluded_dates,
    }


def compare_periods(
        connection: sqlite3.Connection,
        baseline: Period,
        current: Period,
        manual_exclusions: dict[date, str] | None = None,
        include_unchanged: bool = False,
) -> dict:
    validate_period(baseline, "baseline")
    validate_period(current, "current")
    if baseline.weeks != current.weeks:
        raise ValueError("baseline and current periods must contain the same number of weeks")
    if baseline.end >= current.start:
        raise ValueError("baseline period must end before the current period starts")

    try:
        before = prepare_period(connection, baseline, manual_exclusions)
        after = prepare_period(connection, current, manual_exclusions)
    except ComparisonUnavailable as exc:
        return {
            "available": False,
            "reason": str(exc),
            "baseline": {
                "start": compact(baseline.start), "end": compact(baseline.end),
                "weeks": baseline.weeks, "context": baseline.context,
            },
            "current": {
                "start": compact(current.start), "end": compact(current.end),
                "weeks": current.weeks, "context": current.context,
            },
            "changes": [],
        }

    unstable = set(before.unstable_routes) | set(after.unstable_routes)
    keys = (set(before.weekly_counts) | set(after.weekly_counts)) - unstable
    changes = []
    for key in keys:
        baseline_count = before.weekly_counts.get(key, 0)
        current_count = after.weekly_counts.get(key, 0)
        change = current_count - baseline_count
        if change == 0 and not include_unchanged:
            continue
        percentage = (
            round(100.0 * change / baseline_count, 1)
            if baseline_count else None
        )
        baseline_labels = before.labels.get(key, [])
        current_labels = after.labels.get(key, [])
        changes.append({
            "operator": key.operator,
            "route_id": key.route_id,
            "direction": key.direction,
            "route": (
                current_labels[0] if current_labels else
                baseline_labels[0] if baseline_labels else "(unnamed)"
            ),
            "baseline_route_labels": baseline_labels,
            "current_route_labels": current_labels,
            "baseline_weekday_journeys": baseline_count,
            "current_weekday_journeys": current_count,
            "journey_change": change,
            "percentage_change": percentage,
            "status": "gained" if change > 0 else "lost" if change < 0 else "unchanged",
        })
    changes.sort(key=lambda row: (
        row["journey_change"], row["operator"], row["route"],
        row["route_id"], row["direction"],
    ))
    unstable_rows = []
    for key in sorted(unstable):
        unstable_rows.append({
            **asdict(key),
            "baseline_reason": before.unstable_routes.get(key),
            "current_reason": after.unstable_routes.get(key),
        })
    context_verified = (
        baseline.context != "not supplied"
        and baseline.context.casefold() == current.context.casefold()
    )
    return {
        "available": True,
        "baseline": period_payload(before),
        "current": period_payload(after),
        "calendar_context_verified": context_verified,
        "calendar_context_warning": None if context_verified else SEASON_NOTE,
        "unstable_routes_withheld": unstable_rows,
        "changes": changes,
    }


def comparison_report(
        connection: sqlite3.Connection,
        *,
        as_of: date | None,
        weeks: int,
        horizons: tuple[int, ...],
        explicit_periods: tuple[Period, Period] | None,
        manual_exclusions: dict[date, str],
        include_unchanged: bool,
) -> dict:
    validate_database(connection)
    history = trustworthy_history(connection)
    if as_of is None:
        if history["last_snapshot"] is None:
            raise RuntimeError("expected_trips contains no snapshots")
        as_of = parse_date(history["last_snapshot"])
    if weeks < MIN_WEEKS:
        raise ValueError(f"--weeks must be at least {MIN_WEEKS}")

    results = []
    if explicit_periods:
        baseline, current = explicit_periods
        result = compare_periods(
            connection, baseline, current, manual_exclusions, include_unchanged)
        result["label"] = "explicit periods"
        result["months_ago"] = None
        results.append(result)
    else:
        current = period_ending(as_of, weeks)
        for months in horizons:
            shifted = current.end - relativedelta(months=months)
            baseline = period_ending(shifted, weeks)
            result = compare_periods(
                connection, baseline, current, manual_exclusions, include_unchanged)
            result["label"] = f"{months} month" + ("" if months == 1 else "s")
            result["months_ago"] = months
            results.append(result)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": compact(as_of),
        "source": "audit.expected_trips",
        "journey_measure": "scheduled journeys in one representative Monday-Friday week",
        "scope_note": SCOPE_NOTE,
        "season_note": SEASON_NOTE,
        "history": history,
        "comparisons": results,
    }


def format_change(row: dict) -> str:
    percentage = (
        "new" if row["percentage_change"] is None
        else f"{row['percentage_change']:+.1f}%"
    )
    return (
        f"  {row['operator']:<5} {row['route']:<8} "
        f"[route {row['route_id']}, direction {row['direction']}]  "
        f"{row['baseline_weekday_journeys']} -> "
        f"{row['current_weekday_journeys']}  "
        f"({row['journey_change']:+d}, {percentage})"
    )


def render_text(report: dict, limit: int) -> str:
    history = report["history"]
    lines = [
        "Registered weekday journey changes",
        "====================================",
        SCOPE_NOTE,
        "",
        "Trustworthy route-identity history: " + (
            f"{history['first_route_identity_date']} to "
            f"{history['last_route_identity_date']} "
            f"({history['route_identity_days']} days)"
            if history["first_route_identity_date"] else "none"
        ),
    ]
    for result in report["comparisons"]:
        lines.extend(["", result["label"]])
        if not result["available"]:
            lines.append("  UNAVAILABLE: " + result["reason"])
            lines.append(
                f"  Baseline {result['baseline']['start']} to "
                f"{result['baseline']['end']}; current "
                f"{result['current']['start']} to {result['current']['end']}.")
            continue
        baseline = result["baseline"]
        current = result["current"]
        lines.append(
            f"  Baseline {baseline['start']} to {baseline['end']} "
            f"({baseline['usable_days']} usable weekdays); current "
            f"{current['start']} to {current['end']} "
            f"({current['usable_days']} usable weekdays).")
        for name, period in (("Baseline", baseline), ("Current", current)):
            for excluded in period["excluded_dates"]:
                lines.append(
                    f"  {name} excluded {excluded['date']}: {excluded['reason']}.")
        if not result["calendar_context_verified"]:
            lines.append("  CONTEXT CHECK NEEDED: " + SEASON_NOTE)
        withheld = result["unstable_routes_withheld"]
        if withheld:
            lines.append(
                f"  Withheld {len(withheld)} route/direction identities whose "
                "counts changed inside a comparison period.")
        losses = [row for row in result["changes"] if row["journey_change"] < 0]
        gains = [row for row in result["changes"] if row["journey_change"] > 0]
        lines.append("  Largest reductions:")
        lines.extend(format_change(row) for row in losses[:limit])
        if not losses:
            lines.append("    none")
        lines.append("  Largest additions:")
        lines.extend(format_change(row) for row in reversed(gains[-limit:]))
        if not gains:
            lines.append("    none")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, default=AUDIT_DB)
    parser.add_argument("--as-of", type=parse_date)
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    parser.add_argument(
        "--horizons", default=",".join(str(value) for value in DEFAULT_HORIZONS),
        help="comma-separated month horizons (default: 1,3,6,12)")
    parser.add_argument("--baseline-start", type=parse_date)
    parser.add_argument("--baseline-end", type=parse_date)
    parser.add_argument("--current-start", type=parse_date)
    parser.add_argument("--current-end", type=parse_date)
    parser.add_argument("--baseline-context", default="not supplied")
    parser.add_argument("--current-context", default="not supplied")
    parser.add_argument(
        "--exclude-date", type=parse_exclusion, action="append", default=[],
        metavar="YYYYMMDD=REASON",
        help="exclude a one-off or locally exceptional weekday; repeat as needed")
    parser.add_argument("--include-unchanged", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--limit", type=int, default=20)
    return parser


def explicit_periods(args: argparse.Namespace) -> tuple[Period, Period] | None:
    values = (
        args.baseline_start, args.baseline_end,
        args.current_start, args.current_end,
    )
    if not any(values):
        return None
    if not all(values):
        raise ValueError(
            "explicit comparison requires baseline/current start and end dates")
    return (
        Period(args.baseline_start, args.baseline_end, args.baseline_context),
        Period(args.current_start, args.current_end, args.current_context),
    )


def parse_horizons(value: str) -> tuple[int, ...]:
    try:
        horizons = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("--horizons must contain whole month numbers") from exc
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("--horizons must contain positive month numbers")
    return horizons


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        periods = explicit_periods(args)
        horizons = parse_horizons(args.horizons)
        if args.limit < 1:
            raise ValueError("--limit must be at least 1")
        if not args.audit_db.exists():
            raise RuntimeError(f"audit database not found: {args.audit_db}")
        exclusions = dict(args.exclude_date)
        with connect_read_only(args.audit_db) as connection:
            report = comparison_report(
                connection,
                as_of=args.as_of,
                weeks=args.weeks,
                horizons=horizons,
                explicit_periods=periods,
                manual_exclusions=exclusions,
                include_unchanged=args.include_unchanged,
            )
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report, args.limit), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
