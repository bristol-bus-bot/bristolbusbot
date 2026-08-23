#!/usr/bin/env python3
"""Build a cautious, dated local bus evidence pack as HTML, PDF and JSON.

The command reads durable audit rollups. It never labels an unseen trip as a
cancellation, and it withholds timetable-frequency claims unless the registered
route identity and calendar context can support a like-for-like comparison.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from xml.sax.saxutils import escape as xml_escape

from dateutil.relativedelta import relativedelta

from audit_operators import NETWORK_LABEL, SHOW_OPERATORS, operator_name
from audit_targets import target_metadata
import frequency_changes


HERE = Path(__file__).resolve().parent
AUDIT_DB = Path(os.getenv("BBB_AUDIT_DB", HERE / "audit.db"))
OUTPUT_ROOT = Path(os.getenv("BBB_AUDIT_SITE_DIR", HERE / "audit_site")) / "packs"
PUBLIC_BASE_URL = "https://bristol-bus-bot.github.io/weca-bus-audit/packs"
METHODOLOGY_URL = (
    "https://github.com/bristol-bus-bot/weca-bus-audit/blob/main/"
    "AUDIT_METHODOLOGY.md"
)
AUDIT_URL = "https://bristol-bus-bot.github.io/weca-bus-audit/"
DEFAULT_MONTHS = 3
MIN_ROUTE_READINGS = 200
MIN_ROUTE_EVIDENCE_DAY_SHARE = 0.8
ROUTE_PERIOD_TOLERANCE_DAYS = 14
MEASUREMENT_BREAKS = (
    (
        date(2026, 7, 13),
        "the replacement collector changed timetable matching and stale-position handling",
    ),
)


class PackUnavailable(RuntimeError):
    """The requested pack cannot be supported by the available evidence."""


@dataclass(frozen=True)
class Scope:
    kind: str
    values: tuple[str, ...]

    @property
    def display(self) -> str:
        if self.kind == "routes":
            return "Routes " + ", ".join(self.values)
        return self.values[0]

    @property
    def noun(self) -> str:
        return "route group" if self.kind == "routes" else self.kind


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"expected an ISO date in YYYY-MM-DD form, got {value!r}") from exc


def compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def iso_compact(value: str) -> str:
    return datetime.strptime(value, "%Y%m%d").date().isoformat()


def pretty_date(day: date | str) -> str:
    if isinstance(day, str):
        day = datetime.strptime(day, "%Y%m%d").date()
    return f"{day.day} {day.strftime('%B %Y')}"


def slugify(value: str) -> str:
    normal = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normal.casefold()).strip("-")
    return slug[:64].rstrip("-") or "local-area"


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def latest_service_date(connection: sqlite3.Connection, operator: str) -> date:
    row = connection.execute(
        "SELECT MAX(service_date) FROM daily_overall_summary WHERE operator=?",
        (operator,),
    ).fetchone()
    if not row or not row[0]:
        raise PackUnavailable(f"no daily audit summaries exist for {operator}")
    return datetime.strptime(row[0], "%Y%m%d").date()


def month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def measurement_breaks_between(start: date, end: date) -> list[dict]:
    return [
        {"date": day.isoformat(), "reason": reason}
        for day, reason in MEASUREMENT_BREAKS if start < day <= end
    ]


def complete_period(
    latest: date, committee_date: date, months: int, as_of: date | None = None,
) -> tuple[date, date, date, date]:
    if months < 1 or months > 12:
        raise ValueError("months must be between 1 and 12")
    evidence_cutoff = min(latest, as_of or latest, committee_date - timedelta(days=1))
    if evidence_cutoff == month_end(evidence_cutoff):
        end = evidence_cutoff
    else:
        end = evidence_cutoff.replace(day=1) - timedelta(days=1)
    start = (end.replace(day=1) - relativedelta(months=months - 1))
    previous_end = start - timedelta(days=1)
    previous_start = previous_end.replace(day=1) - relativedelta(months=months - 1)
    return start, end, previous_start, previous_end


def _canonical_geo(
    connection: sqlite3.Connection, kind: str, value: str, operator: str,
) -> str:
    rows = connection.execute(
        """SELECT DISTINCT geo_key FROM daily_geo_summary
             WHERE operator=? AND geo_type=? AND LOWER(geo_key)=LOWER(?)
             ORDER BY geo_key""",
        (operator, kind, value.strip()),
    ).fetchall()
    if not rows:
        examples = [
            row[0] for row in connection.execute(
                """SELECT DISTINCT geo_key FROM daily_geo_summary
                     WHERE operator=? AND geo_type=? ORDER BY geo_key LIMIT 12""",
                (operator, kind),
            )
        ]
        hint = ", ".join(examples) if examples else "none available"
        raise PackUnavailable(f"unknown {kind} {value!r}; examples: {hint}")
    return rows[0][0]


def _canonical_routes(
    connection: sqlite3.Connection, values: list[str], operator: str,
) -> tuple[str, ...]:
    result = []
    for value in values:
        row = connection.execute(
            """SELECT route FROM daily_route_summary
                 WHERE operator=? AND LOWER(route)=LOWER(?)
                 ORDER BY service_date DESC LIMIT 1""",
            (operator, value.strip()),
        ).fetchone()
        if not row:
            raise PackUnavailable(f"route {value!r} has no audit summary")
        if row[0] not in result:
            result.append(row[0])
    if not result:
        raise PackUnavailable("at least one route is required")
    return tuple(result)


def resolve_scope(
    connection: sqlite3.Connection, args: argparse.Namespace,
) -> Scope:
    if args.area:
        return Scope(
            "area", (_canonical_geo(
                connection, "area", args.area, args.operator),))
    if args.ward:
        return Scope(
            "ward", (_canonical_geo(
                connection, "ward", args.ward, args.operator),))
    return Scope(
        "routes", _canonical_routes(connection, args.route, args.operator))


def _aggregate_rows(rows: list[sqlite3.Row]) -> dict:
    readings = sum(int(row["readings"] or 0) for row in rows)
    on_time = sum(int(row["on_time"] or 0) for row in rows)
    return {
        "service_days": len({row["service_date"] for row in rows}),
        "readings": readings,
        "on_time": on_time,
        "not_on_time": readings - on_time,
        "on_time_pct": round(100.0 * on_time / readings, 1) if readings else None,
    }


def scope_daily_rows(
    connection: sqlite3.Connection,
    scope: Scope,
    operator: str,
    start: date,
    end: date,
) -> list[sqlite3.Row]:
    if scope.kind in ("area", "ward"):
        return connection.execute(
            """SELECT service_date, readings_in_gate AS readings, on_time
                 FROM daily_geo_summary
                WHERE operator=? AND geo_type=? AND geo_key=?
                  AND service_date BETWEEN ? AND ?
                ORDER BY service_date""",
            (operator, scope.kind, scope.values[0], compact(start), compact(end)),
        ).fetchall()
    placeholders = ",".join("?" for _ in scope.values)
    return connection.execute(
        f"""SELECT service_date,
                    SUM(readings_in_gate) AS readings, SUM(on_time) AS on_time
               FROM daily_route_summary
              WHERE operator=? AND route IN ({placeholders})
                AND service_date BETWEEN ? AND ?
              GROUP BY service_date ORDER BY service_date""",
        (operator, *scope.values, compact(start), compact(end)),
    ).fetchall()


def available_audit_days(
    connection: sqlite3.Connection, operator: str, start: date, end: date,
) -> int:
    return int(connection.execute(
        """SELECT COUNT(DISTINCT service_date) FROM daily_overall_summary
             WHERE operator=? AND service_date BETWEEN ? AND ?""",
        (operator, compact(start), compact(end)),
    ).fetchone()[0])


def aggregate_scope(
    connection: sqlite3.Connection,
    scope: Scope,
    operator: str,
    start: date,
    end: date,
) -> dict:
    rows = scope_daily_rows(connection, scope, operator, start, end)
    result = _aggregate_rows(rows)
    result.update({
        "start": start.isoformat(),
        "end": end.isoformat(),
        "available_audit_days": available_audit_days(
            connection, operator, start, end),
    })
    result["day_share_pct"] = (
        round(100.0 * result["service_days"] / result["available_audit_days"], 1)
        if result["available_audit_days"] else None
    )
    return result


def monthly_scope(
    connection: sqlite3.Connection,
    scope: Scope,
    operator: str,
    start: date,
    end: date,
) -> list[dict]:
    rows = scope_daily_rows(connection, scope, operator, start, end)
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["service_date"][:6], []).append(row)
    result = []
    for key in sorted(grouped):
        aggregate = _aggregate_rows(grouped[key])
        month = datetime.strptime(key, "%Y%m").date()
        aggregate.update({
            "month": month.strftime("%B %Y"),
            "month_key": f"{month.year:04d}-{month.month:02d}",
        })
        result.append(aggregate)
    return result


def route_summaries(
    connection: sqlite3.Connection,
    scope: Scope,
    operator: str,
    start: date,
    end: date,
    min_readings: int,
) -> dict:
    if scope.kind in ("area", "ward"):
        if not table_exists(connection, "daily_geo_route_summary"):
            return {
                "available": False,
                "reason": "route-by-area history has not started yet",
                "complete_period": False,
                "evidence_days": 0,
                "headline_days": 0,
                "day_share_pct": None,
                "minimum_readings": min_readings,
                "rows": [],
            }
        rows = connection.execute(
            """SELECT source_operator, route,
                      COUNT(DISTINCT service_date) AS service_days,
                      SUM(readings_in_gate) AS readings, SUM(on_time) AS on_time,
                      MIN(service_date) AS first_date, MAX(service_date) AS last_date
                 FROM daily_geo_route_summary
                WHERE operator=? AND geo_type=? AND geo_key=?
                  AND service_date BETWEEN ? AND ?
                GROUP BY source_operator, route""",
            (operator, scope.kind, scope.values[0], compact(start), compact(end)),
        ).fetchall()
    else:
        placeholders = ",".join("?" for _ in scope.values)
        source_operators = SHOW_OPERATORS if operator == NETWORK_LABEL else [operator]
        operator_placeholders = ",".join("?" for _ in source_operators)
        rows = connection.execute(
            f"""SELECT operator AS source_operator, route,
                       COUNT(DISTINCT service_date) AS service_days,
                       SUM(readings_in_gate) AS readings, SUM(on_time) AS on_time,
                       MIN(service_date) AS first_date, MAX(service_date) AS last_date
                  FROM daily_route_summary
                 WHERE operator IN ({operator_placeholders})
                   AND route IN ({placeholders})
                   AND service_date BETWEEN ? AND ?
                 GROUP BY operator, route""",
            (*source_operators, *scope.values, compact(start), compact(end)),
        ).fetchall()
    headline_days = aggregate_scope(
        connection, scope, operator, start, end)["service_days"]
    output = []
    evidence_days = set()
    for row in rows:
        readings = int(row["readings"] or 0)
        on_time = int(row["on_time"] or 0)
        output.append({
            "operator": row["source_operator"],
            "operator_name": operator_name(row["source_operator"]),
            "route": row["route"],
            "display": (
                f"{row['route']} ({operator_name(row['source_operator'])})"),
            "service_days": int(row["service_days"] or 0),
            "readings": readings,
            "on_time": on_time,
            "on_time_pct": round(100.0 * on_time / readings, 1) if readings else None,
            "thin_sample": readings < min_readings,
            "first_date": iso_compact(row["first_date"]),
            "last_date": iso_compact(row["last_date"]),
            "partial_period": (
                datetime.strptime(row["first_date"], "%Y%m%d").date()
                > start + timedelta(days=ROUTE_PERIOD_TOLERANCE_DAYS)
                or datetime.strptime(row["last_date"], "%Y%m%d").date()
                < end - timedelta(days=ROUTE_PERIOD_TOLERANCE_DAYS)
            ),
        })
        for day in connection.execute(
            ("""SELECT DISTINCT service_date FROM daily_geo_route_summary
                  WHERE operator=? AND geo_type=? AND geo_key=?
                    AND source_operator=? AND route=?
                    AND service_date BETWEEN ? AND ?"""
             if scope.kind in ("area", "ward") else
             """SELECT DISTINCT service_date FROM daily_route_summary
                  WHERE operator=? AND route=?
                    AND service_date BETWEEN ? AND ?"""),
            ((operator, scope.kind, scope.values[0], row["source_operator"],
              row["route"],
              compact(start), compact(end))
             if scope.kind in ("area", "ward") else
             (row["source_operator"], row["route"],
              compact(start), compact(end))),
        ):
            evidence_days.add(day[0])
    output.sort(key=lambda item: (
        item["on_time_pct"] is None,
        item["on_time_pct"] if item["on_time_pct"] is not None else 999,
        -item["readings"], item["operator"], item["route"],
    ))
    share = len(evidence_days) / headline_days if headline_days else 0
    return {
        "available": bool(output),
        "reason": None if output else "no route readings in this period",
        "complete_period": share >= MIN_ROUTE_EVIDENCE_DAY_SHARE,
        "evidence_days": len(evidence_days),
        "headline_days": headline_days,
        "day_share_pct": round(share * 100.0, 1) if headline_days else None,
        "minimum_readings": min_readings,
        "rows": output,
    }


def frequency_summary(
    connection: sqlite3.Connection,
    scope: Scope,
    local_routes: set[tuple[str, str]],
    period_end: date,
    months: int,
    calendar_context: str | None,
) -> dict:
    if not calendar_context:
        return {
            "available": False,
            "reason": (
                "withheld because the two periods' calendar context, including "
                "school-term or seasonal differences, has not been checked"),
            "changes": [],
        }
    if not table_exists(connection, "expected_trips"):
        return {
            "available": False,
            "reason": "registered timetable snapshots are unavailable",
            "changes": [],
        }
    current = frequency_changes.period_ending(
        period_end, frequency_changes.DEFAULT_WEEKS, calendar_context)
    baseline = frequency_changes.period_ending(
        current.end - relativedelta(months=months),
        frequency_changes.DEFAULT_WEEKS,
        calendar_context,
    )
    comparison = frequency_changes.compare_periods(
        connection, baseline, current, include_unchanged=False)
    if not comparison["available"]:
        return {
            "available": False,
            "reason": comparison["reason"],
            "baseline": comparison["baseline"],
            "current": comparison["current"],
            "changes": [],
        }
    wanted = {
        (operator.casefold(), route.casefold())
        for operator, route in local_routes
    }
    changes = [
        row for row in comparison["changes"]
        if (row["operator"].casefold(), row["route"].casefold()) in wanted
    ]
    changes.sort(key=lambda row: (
        row["journey_change"], row["operator"], row["route"],
        row["direction"],
    ))
    return {
        "available": True,
        "reason": None,
        "baseline": comparison["baseline"],
        "current": comparison["current"],
        "calendar_context": calendar_context,
        "measure": (
            "registered journeys in one representative Monday-Friday week"),
        "changes": changes,
        "unchanged_or_out_of_scope_note": (
            "Only changed route/direction records serving this scope are shown."),
    }


def suggested_questions(report: dict) -> list[str]:
    headline = report["headline"]
    target = report["target"]["current_target_pct"]
    if not report["target"]["period_target_consistent"]:
        first_question = (
            f"{headline['on_time_pct']:.1f}% of {headline['readings']:,} "
            f"timing-point readings in {report['scope']['display']} were on "
            f"time. The target changed during the period and was {target}% at "
            "its end. How does WECA assess performance across that change?")
    elif headline["on_time_pct"] < target:
        first_question = (
            f"Only {headline['on_time_pct']:.1f}% of {headline['readings']:,} "
            f"timing-point readings in {report['scope']['display']} were on "
            f"time during this period, against the {target}% area-wide target. "
            "What specific action is being taken for passengers in this area?")
    else:
        first_question = (
            f"{headline['on_time_pct']:.1f}% of {headline['readings']:,} "
            f"timing-point readings in {report['scope']['display']} were on "
            f"time, above the {target}% area-wide target. Does WECA's own data "
            "confirm this, and what helped performance here?")
    questions = [first_question]
    eligible = [
        row for row in report["routes"]["rows"]
        if not row["thin_sample"] and not row["partial_period"]
        and row["on_time_pct"] is not None
    ]
    if eligible:
        worst = eligible[0]
        questions.append(
            f"{worst['operator_name']} route {worst['route']} was on time for "
            f"{worst['on_time_pct']:.1f}% "
            f"of {worst['readings']:,} local readings. Does WECA's own "
            "route-level monitoring show the same problem, and is there an "
            "action plan?"
        )
    else:
        questions.append(
            f"The route table contains no route with at least "
            f"{report['routes']['minimum_readings']:,} readings for the full "
            "period. What route-level evidence does WECA use when local public "
            "samples are this thin?"
        )
    changes = report["frequency"]["changes"]
    if report["frequency"]["available"] and changes:
        change = changes[0]
        questions.append(
            f"The registered timetable for {operator_name(change['operator'])} "
            f"route {change['route']} direction "
            f"{change['direction']} changed from "
            f"{change['baseline_weekday_journeys']} to "
            f"{change['current_weekday_journeys']} weekday journeys in the "
            "comparison. What passenger need assessment supported that change?"
        )
    elif report["change_from_previous_pct_points"] is not None:
        previous = report["previous"]
        delta = report["change_from_previous_pct_points"]
        direction = "higher" if delta >= 0 else "lower"
        questions.append(
            f"On-time performance was {abs(delta):.1f} percentage points "
            f"{direction} than the preceding {report['months']}-month period "
            f"({previous['on_time_pct']:.1f}% from {previous['readings']:,} "
            "readings). What does WECA believe caused that change?"
        )
    else:
        questions.append(
            f"This pack contains {headline['readings']:,} readings across "
            f"{headline['service_days']} service days. Will WECA publish its "
            "own monthly route-level scheduled and lost-mileage figures so the "
            "two sources can be compared?"
        )
    return questions[:3]


def build_report(
    connection: sqlite3.Connection,
    scope: Scope,
    operator: str,
    committee_date: date,
    months: int,
    as_of: date | None,
    min_route_readings: int,
    frequency_context: str | None,
    public_base_url: str,
) -> dict:
    latest = latest_service_date(connection, operator)
    start, end, previous_start, previous_end = complete_period(
        latest, committee_date, months, as_of)
    headline = aggregate_scope(connection, scope, operator, start, end)
    if not headline["readings"]:
        raise PackUnavailable(
            f"no readings for {scope.display} between {start} and {end}")
    previous = aggregate_scope(
        connection, scope, operator, previous_start, previous_end)
    routes = route_summaries(
        connection, scope, operator, start, end, min_route_readings)
    route_names = {
        (row["operator"], row["route"]) for row in routes["rows"]}
    frequency = frequency_summary(
        connection, scope, route_names, end, months, frequency_context)
    target = target_metadata(compact(end))
    start_target = target_metadata(compact(start))
    target["period_target_consistent"] = (
        start_target["current_target_financial_year"]
        == target["current_target_financial_year"])
    target["period_start_target_pct"] = start_target["current_target_pct"]
    target["period_start_target_financial_year"] = (
        start_target["current_target_financial_year"])
    slug = (
        f"{committee_date.isoformat()}-{scope.kind}-"
        f"{slugify('-'.join(scope.values))}"
    )
    public_url = public_base_url.rstrip("/") + "/" + slug + "/"
    comparability_breaks = measurement_breaks_between(previous_start, end)
    delta = (
        round(headline["on_time_pct"] - previous["on_time_pct"], 1)
        if previous["on_time_pct"] is not None and not comparability_breaks else None
    )
    limitations = [
        "These are independent estimates from open data, not official figures.",
        "A reading is an observation at a timetable timing point, not a unique bus journey or passenger.",
        "Only readings within 150 metres of the timing point are counted; origin readings are excluded.",
        "Missing tracker observations are not labelled as cancellations.",
        "Area and ward route tables describe where qualifying readings were observed, not every place a route may serve.",
        "Registered timetable changes describe what was scheduled, not why it changed or whether each journey ran.",
    ]
    if comparability_breaks:
        limitations.insert(
            1,
            "The preceding-period change is withheld because the audit method changed within the comparison window.",
        )
    if not target["period_target_consistent"]:
        limitations.insert(
            2,
            "The official area-wide target changed during this evidence period; the headline card shows the target at the period end.",
        )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "slug": slug,
        "public_url": public_url,
        "committee_date": committee_date.isoformat(),
        "months": months,
        "scope": {
            "kind": scope.kind,
            "values": list(scope.values),
            "display": scope.display,
        },
        "operator": {"code": operator, "name": operator_name(operator)},
        "latest_audit_date": latest.isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_period": {
            "start": previous_start.isoformat(),
            "end": previous_end.isoformat(),
        },
        "target": target,
        "headline": headline,
        "previous": previous,
        "change_from_previous_pct_points": delta,
        "change_unavailable_reason": (
            "withheld because the audit method changed within the two periods"
            if comparability_breaks else
            "no comparable earlier readings" if previous["on_time_pct"] is None
            else None
        ),
        "comparability_breaks": comparability_breaks,
        "monthly": monthly_scope(connection, scope, operator, start, end),
        "routes": routes,
        "frequency": frequency,
        "questions": [],
        "limitations": limitations,
        "sources": {
            "audit": AUDIT_URL,
            "methodology": METHODOLOGY_URL,
            "target": target["current_target_source_url"],
        },
    }
    report["questions"] = suggested_questions(report)
    return report


def _pct(value: float | None) -> str:
    return "not available" if value is None else f"{value:.1f}%"


def _change(value: float | None) -> str:
    if value is None:
        return "No comparable earlier period"
    return f"{value:+.1f} percentage points"


def _change_text(report: dict) -> str:
    value = report["change_from_previous_pct_points"]
    if value is not None:
        return _change(value)
    return report["change_unavailable_reason"] or "No comparable earlier period"


def _change_summary(report: dict) -> str:
    text = _change_text(report)
    if report["change_from_previous_pct_points"] is None:
        return text
    return f"{text} compared with the preceding {report['months']}-month period"


def _route_confidence(row: dict) -> str:
    if row["partial_period"]:
        return "Partial period"
    if row["thin_sample"]:
        return "Indicative"
    return "Usable sample"


def render_html(report: dict) -> str:
    e = lambda value: html.escape(str(value), quote=True)
    headline = report["headline"]
    target = report["target"]["current_target_pct"]
    route_rows = report["routes"]["rows"][:8]
    route_table = "".join(
        "<tr>"
        f"<td><strong>{e(row['display'])}</strong></td>"
        f"<td>{e(_pct(row['on_time_pct']))}</td>"
        f"<td>{row['readings']:,}</td>"
        f"<td>{e(_route_confidence(row))}</td>"
        "</tr>"
        for row in route_rows
    ) or '<tr><td colspan="4">No route-level result is available.</td></tr>'
    monthly_rows = "".join(
        "<tr>"
        f"<td>{e(row['month'])}</td>"
        f"<td>{e(_pct(row['on_time_pct']))}</td>"
        f"<td>{row['readings']:,}</td>"
        f"<td>{row['service_days']}</td>"
        "</tr>"
        for row in report["monthly"]
    )
    frequency = report["frequency"]
    if frequency["available"] and frequency["changes"]:
        frequency_rows = "".join(
            "<tr>"
            f"<td>{e(row['route'])}</td><td>{row['direction']}</td>"
            f"<td>{row['baseline_weekday_journeys']}</td>"
            f"<td>{row['current_weekday_journeys']}</td>"
            f"<td>{row['journey_change']:+d}</td>"
            "</tr>"
            for row in frequency["changes"][:8]
        )
        frequency_html = f"""
        <table><thead><tr><th>Service</th><th>Direction</th><th>Before</th>
        <th>Now</th><th>Change</th></tr></thead><tbody>{frequency_rows}</tbody></table>
        <p class="note">{e(frequency['measure'])}. Calendar context: {e(frequency['calendar_context'])}.</p>"""
    elif frequency["available"]:
        frequency_html = (
            '<p class="withheld">No changed registered journey counts were '
            'found for the selected routes in the checked comparison.</p>')
    else:
        frequency_html = (
            f'<p class="withheld"><strong>Not included:</strong> '
            f'{e(frequency["reason"])}.</p>')
    questions = "".join(f"<li>{e(question)}</li>" for question in report["questions"])
    limitations = "".join(
        f"<li>{e(item)}</li>" for item in report["limitations"])
    route_warning = ""
    if not report["routes"]["complete_period"]:
        share = report["routes"].get("day_share_pct")
        detail = f" ({share:.1f}% of scope days)" if share is not None else ""
        route_warning = (
            '<p class="withheld"><strong>Route evidence is incomplete for the '
            f'full period{detail}.</strong> Treat the route table as indicative; '
            'the area headline still uses the durable geography totals.</p>')
    comparison_warning = ""
    if report["comparability_breaks"]:
        breaks = "; ".join(
            f"{pretty_date(date.fromisoformat(item['date']))}: {item['reason']}"
            for item in report["comparability_breaks"]
        )
        comparison_warning = (
            '<p class="withheld"><strong>Period change withheld:</strong> '
            f'{e(breaks)}.</p>')
    target_label = "target"
    target_warning = ""
    if not report["target"]["period_target_consistent"]:
        target_label = "target at period end"
        target_warning = (
            '<p class="withheld"><strong>The target changed during this '
            f'period:</strong> {report["target"]["period_start_target_pct"]}% '
            f'({e(report["target"]["period_start_target_financial_year"])}) to '
            f'{target}% ({e(report["target"]["current_target_financial_year"])}).'
            '</p>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local bus evidence - {e(report['scope']['display'])}</title>
<style>
:root{{--ink:#14201d;--muted:#58645f;--paper:#f7f3e8;--card:#fffdf6;--green:#0b6b53;--lime:#cce85b;--amber:#e7a92f;--red:#b73a3a;--line:#d9d4c7}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.48 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:860px;margin:auto;padding:24px}} header{{background:var(--ink);color:white;padding:28px;border-radius:22px;position:relative;overflow:hidden}}
header:after{{content:"";position:absolute;width:190px;height:190px;border-radius:50%;background:var(--lime);right:-80px;top:-95px;opacity:.9}}
.eyebrow{{text-transform:uppercase;letter-spacing:.12em;font-size:.75rem;color:var(--lime);font-weight:800}} h1{{font-size:clamp(2rem,8vw,3.8rem);line-height:.98;margin:.4rem 0 1rem;max-width:650px}}
h2{{margin:2.2rem 0 .7rem;font-size:1.45rem}} p{{margin:.55rem 0}} .period{{color:#d5ded9;max-width:620px}} .cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}} .card b{{display:block;font-size:1.75rem;color:var(--green)}} .card span{{color:var(--muted);font-size:.88rem}}
.callout,.withheld{{border-left:5px solid var(--amber);background:#fff8df;padding:13px 15px;border-radius:7px;margin:14px 0}} .callout{{border-color:var(--lime);background:#f3f8d9}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);font-size:.92rem}} th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line)}} th{{background:#ece8dc;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}}
ol.questions{{padding-left:1.35rem}} ol.questions li{{margin:0 0 1rem;padding-left:.35rem;font-weight:650}} .note,.small{{font-size:.86rem;color:var(--muted)}} .links a{{color:var(--green)}} footer{{border-top:1px solid var(--line);margin-top:2rem;padding-top:1rem;color:var(--muted);font-size:.82rem}}
@media(max-width:620px){{main{{padding:12px}}header{{padding:22px;border-radius:16px}}.cards{{grid-template-columns:1fr}}table{{font-size:.82rem}}th,td{{padding:8px 6px}}}}
@media print{{body{{background:white}}main{{max-width:none;padding:0}}header{{border-radius:0}}.card,table{{break-inside:avoid}}a{{color:inherit}}}}
</style></head><body><main>
<header><div class="eyebrow">Independent local bus evidence</div>
<h1>{e(report['scope']['display'])}</h1>
<p class="period">What was happening from {e(pretty_date(report['period']['start'].replace('-', '')))} to {e(pretty_date(report['period']['end'].replace('-', '')))}, prepared for {e(pretty_date(date.fromisoformat(report['committee_date'])))}.</p></header>
<div class="callout"><strong>Read this first:</strong> these are independent estimates from open data, not official operator or WECA figures. They measure timing-point observations, not cancellations.</div>
<section class="cards"><div class="card"><b>{headline['on_time_pct']:.1f}%</b><span>on time</span></div>
<div class="card"><b>{headline['readings']:,}</b><span>qualifying readings</span></div>
<div class="card"><b>{target}%</b><span>WECA {e(report['target']['current_target_financial_year'])} {e(target_label)}</span></div></section>
{target_warning}
<p><strong>Change:</strong> {e(_change_summary(report))}.</p>
{comparison_warning}
<h2>Month by month</h2><table><thead><tr><th>Month</th><th>On time</th><th>Readings</th><th>Days</th></tr></thead><tbody>{monthly_rows}</tbody></table>
<h2>Routes seen in this area</h2>{route_warning}<table><thead><tr><th>Service</th><th>On time</th><th>Readings</th><th>Confidence</th></tr></thead><tbody>{route_table}</tbody></table>
<p class="note">Routes are ordered from lowest on-time percentage. A route needs {report['routes']['minimum_readings']:,} readings before this pack treats its figure as more than indicative.</p>
<h2>Registered timetable changes</h2>{frequency_html}
<h2>Three questions to take into the meeting</h2><ol class="questions">{questions}</ol>
<h2>What these figures do not prove</h2><ul>{limitations}</ul>
<p class="links"><a href="{e(report['sources']['audit'])}">Explore the audit</a> - <a href="{e(report['sources']['methodology'])}">Read the full methodology and limitations</a> - <a href="{e(report['sources']['target'])}">Check the target source</a></p>
<footer>Generated {e(report['generated_at'])}. Permanent pack address: {e(report['public_url'])}</footer>
</main></body></html>"""


def _pdf_safe(value: object) -> str:
    text = str(value).replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u00a0", " ")
    return xml_escape(text)


def render_pdf(report: dict, output: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    ink = colors.HexColor("#14201d")
    green = colors.HexColor("#0b6b53")
    lime = colors.HexColor("#cce85b")
    paper = colors.HexColor("#f7f3e8")
    muted = colors.HexColor("#58645f")
    line = colors.HexColor("#d9d4c7")
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "PackTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=25, leading=26, textColor=colors.white, alignment=TA_LEFT,
        spaceAfter=3,
    )
    eyebrow = ParagraphStyle(
        "Eyebrow", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8, leading=10, textColor=lime, spaceAfter=5,
    )
    header_body = ParagraphStyle(
        "HeaderBody", parent=styles["Normal"], fontSize=9.5, leading=13,
        textColor=colors.HexColor("#d5ded9"),
    )
    heading = ParagraphStyle(
        "Heading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=14, leading=16, textColor=ink, spaceBefore=7, spaceAfter=4,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontSize=8.7, leading=11.5,
        textColor=ink, spaceAfter=3,
    )
    small = ParagraphStyle(
        "Small", parent=body, fontSize=7.2, leading=9, textColor=muted,
    )
    question = ParagraphStyle(
        "Question", parent=body, fontName="Helvetica-Bold", fontSize=9.2,
        leading=12, leftIndent=7 * mm, firstLineIndent=-5 * mm, spaceAfter=5,
    )
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm,
        title=f"Local bus evidence - {report['scope']['display']}",
        author="Bristol Bus Bot",
    )

    def page(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(line)
        canvas.line(16 * mm, 11 * mm, A4[0] - 16 * mm, 11 * mm)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(16 * mm, 7 * mm, "Independent estimates from open data")
        canvas.drawRightString(
            A4[0] - 16 * mm, 7 * mm, f"Page {document.page}")
        canvas.restoreState()

    start_text = pretty_date(report["period"]["start"].replace("-", ""))
    end_text = pretty_date(report["period"]["end"].replace("-", ""))
    committee_text = pretty_date(date.fromisoformat(report["committee_date"]))
    header = Table([[ [
        Paragraph("INDEPENDENT LOCAL BUS EVIDENCE", eyebrow),
        Paragraph(_pdf_safe(report["scope"]["display"]), title),
        Paragraph(
            _pdf_safe(
                f"What was happening from {start_text} to {end_text}, "
                f"prepared for {committee_text}."),
            header_body,
        ),
    ] ]], colWidths=[A4[0] - 32 * mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ink),
        ("BOX", (0, 0), (-1, -1), 0, ink),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story = [header, Spacer(1, 5)]
    caution = Table([[
        Paragraph(
            "<b>Read this first:</b> These are independent estimates from "
            "open data, not official operator or WECA figures. They measure "
            "timing-point observations, not cancellations.", body)
    ]], colWidths=[A4[0] - 32 * mm])
    caution.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f8d9")),
        ("LINEBEFORE", (0, 0), (0, -1), 4, lime),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.extend([caution, Spacer(1, 5)])
    headline = report["headline"]
    cards = Table([
        [Paragraph(f"<b>{headline['on_time_pct']:.1f}%</b><br/><font size='7'>ON TIME</font>", body),
         Paragraph(f"<b>{headline['readings']:,}</b><br/><font size='7'>QUALIFYING READINGS</font>", body),
         Paragraph(f"<b>{report['target']['current_target_pct']}%</b><br/><font size='7'>WECA TARGET</font>", body)]
    ], colWidths=[(A4[0] - 36 * mm) / 3] * 3, hAlign="LEFT")
    cards.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, -1), green),
        ("BOX", (0, 0), (-1, -1), .5, line),
        ("INNERGRID", (0, 0), (-1, -1), .5, line),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        cards,
        Paragraph(
            "<b>Change:</b> " + _pdf_safe(_change_summary(report)) + ".",
            body,
        ),
    ])
    if not report["target"]["period_target_consistent"]:
        story.append(Paragraph(
            "<b>Target changed during this period:</b> "
            f"{report['target']['period_start_target_pct']}% "
            f"({report['target']['period_start_target_financial_year']}) to "
            f"{report['target']['current_target_pct']}% "
            f"({report['target']['current_target_financial_year']}); the card "
            "shows the target at the period end.", body))
    if report["comparability_breaks"]:
        breaks = "; ".join(
            f"{pretty_date(date.fromisoformat(item['date']))}: {item['reason']}"
            for item in report["comparability_breaks"]
        )
        story.append(Paragraph(
            "<b>Period change withheld:</b> " + _pdf_safe(breaks) + ".", body))
    story.append(Paragraph("Month by month", heading))
    month_data = [["Month", "On time", "Readings", "Days"]] + [
        [row["month"], _pct(row["on_time_pct"]), f"{row['readings']:,}",
         str(row["service_days"])] for row in report["monthly"]
    ]
    monthly = Table(month_data, colWidths=[62 * mm, 35 * mm, 40 * mm, 25 * mm])
    monthly.setStyle(_pdf_table_style(colors, ink, paper, line))
    story.extend([monthly, Paragraph("Routes seen in this area", heading)])
    if not report["routes"]["complete_period"]:
        story.append(Paragraph(
            "<b>Route evidence is incomplete for the full period.</b> Treat "
            "the route table as indicative; the area headline uses the "
            "durable geography totals.", body))
    route_data = [["Service", "On time", "Readings", "Confidence"]]
    for row in report["routes"]["rows"][:8]:
        route_data.append([
            _pdf_safe(row["display"]), _pct(row["on_time_pct"]),
            f"{row['readings']:,}",
            _route_confidence(row),
        ])
    if len(route_data) == 1:
        route_data.append(["No route result", "-", "-", "-"])
    routes = Table(route_data, colWidths=[47 * mm, 34 * mm, 35 * mm, 46 * mm])
    routes.setStyle(_pdf_table_style(colors, ink, paper, line))
    story.extend([
        routes,
        Paragraph(
            f"A route needs {report['routes']['minimum_readings']:,} readings "
            "before this pack treats its figure as more than indicative.", small),
        Paragraph("Registered timetable changes", heading),
    ])
    frequency = report["frequency"]
    if frequency["available"] and frequency["changes"]:
        freq_data = [["Route", "Direction", "Before", "Now", "Change"]] + [
            [row["route"], str(row["direction"]),
             str(row["baseline_weekday_journeys"]),
             str(row["current_weekday_journeys"]),
             f"{row['journey_change']:+d}"]
            for row in frequency["changes"][:8]
        ]
        freq_table = Table(
            freq_data, colWidths=[55 * mm, 30 * mm, 28 * mm, 24 * mm, 28 * mm])
        freq_table.setStyle(_pdf_table_style(colors, ink, paper, line))
        story.extend([freq_table, Paragraph(
            _pdf_safe(
                f"{frequency['measure']}. Calendar context: "
                f"{frequency['calendar_context']}."), small)])
    else:
        message = (
            "No changed registered journey counts were found for the selected "
            "routes in the checked comparison."
            if frequency["available"] else
            "Not included: " + frequency["reason"] + "."
        )
        story.append(Paragraph(_pdf_safe(message), body))
    story.append(Paragraph("Three questions to take into the meeting", heading))
    for index, item in enumerate(report["questions"], 1):
        story.append(Paragraph(
            f"{index}. {_pdf_safe(item)}", question))
    story.append(Paragraph("What these figures do not prove", heading))
    for item in report["limitations"]:
        story.append(Paragraph(f"- {_pdf_safe(item)}", body))
    story.extend([
        Spacer(1, 4),
        Paragraph(
            f"Full audit: <link href='{AUDIT_URL}' color='#0b6b53'>{AUDIT_URL}</link><br/>"
            f"Methodology: <link href='{METHODOLOGY_URL}' color='#0b6b53'>{METHODOLOGY_URL}</link>",
            small,
        ),
        Paragraph(
            _pdf_safe(
                f"Generated {report['generated_at']}. Permanent pack address: "
                f"{report['public_url']}"), small),
    ])
    doc.build(story, onFirstPage=page, onLaterPages=page)


def _pdf_table_style(colors, ink, paper, line):
    from reportlab.platypus import TableStyle
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), paper),
        ("TEXTCOLOR", (0, 0), (-1, 0), ink),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), .5, line),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ])


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_pack(report: dict, output_root: Path, replace: bool = False) -> Path:
    output = output_root / report["slug"]
    if output.exists() and not replace:
        raise FileExistsError(
            f"{output} already exists; dated packs are immutable by default "
            "(use --replace only to correct a checked pack)")
    output.mkdir(parents=True, exist_ok=True)
    atomic_text(output / "data.json", json.dumps(report, indent=2) + "\n")
    atomic_text(output / "index.html", render_html(report))
    temporary_pdf = output / "briefing.pdf.tmp"
    render_pdf(report, temporary_pdf)
    os.replace(temporary_pdf, output / "briefing.pdf")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, default=AUDIT_DB)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--area")
    scope.add_argument("--ward")
    scope.add_argument(
        "--route", action="append",
        help="route number/name; repeat for a small route group")
    parser.add_argument("--committee-date", type=parse_iso_date, required=True)
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument("--as-of", type=parse_iso_date)
    parser.add_argument("--operator", default=NETWORK_LABEL)
    parser.add_argument("--minimum-route-readings", type=int, default=MIN_ROUTE_READINGS)
    parser.add_argument(
        "--frequency-context",
        help="checked shared context such as 'school term'; without it frequency changes are withheld")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--public-base-url", default=PUBLIC_BASE_URL)
    parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.minimum_route_readings < 1:
        raise SystemExit("--minimum-route-readings must be positive")
    try:
        connection = connect_read_only(args.audit_db)
    except sqlite3.OperationalError as exc:
        print(f"ERROR: cannot open audit database: {exc}")
        return 1
    try:
        scope = resolve_scope(connection, args)
        report = build_report(
            connection, scope, args.operator, args.committee_date, args.months,
            args.as_of, args.minimum_route_readings, args.frequency_context,
            args.public_base_url,
        )
        output = write_pack(report, args.output_root, args.replace)
    except (PackUnavailable, ValueError, FileExistsError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        connection.close()
    print(f"Wrote {output / 'index.html'}")
    print(f"Wrote {output / 'briefing.pdf'}")
    print(f"Wrote {output / 'data.json'}")
    print(f"Permanent URL after the next audit publish: {report['public_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
