#!/usr/bin/env python3
"""
Export the rollup summary tables to a static JSON file for the public site.

Multi-operator: each day carries a by_operator map keyed on operator code, plus
the pooled network under the NETWORK_LABEL ("ALL"). The site defaults to the
network view and lets the reader switch operator.

This is the only data that needs to leave the Pi. The site is fully static
(HTML plus this JSON) and the Pi never accepts inbound connections. Run daily
after the rollup, then publish audit_site/audit_data.json. Read-only against
audit.db. Run from the bristol-live-buses folder:
    python audit_export.py
"""
import os
import json
import sqlite3
from datetime import datetime, timezone

from audit_operators import SHOW_OPERATORS, NETWORK_LABEL, operator_name, public_route
from audit_publication import publication_exclusions
from snapshot_quality import denominator_reasons, withheld_routes
from sample_quality import qualify_row
from audit_scope import operator_composition, frequency_adherence
from audit_targets import target_metadata

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DB = os.getenv("BBB_AUDIT_DB", os.path.join(HERE, "audit.db"))
OUT_DIR = os.getenv("BBB_AUDIT_SITE_DIR", os.path.join(HERE, "audit_site"))
OUT_FILE = os.path.join(OUT_DIR, "audit_data.json")
HOURLY_DIR = "hourly"
# Hourly figures pool the most recent published days: one day gives most
# route/hour cells only a handful of readings.
HOURLY_WINDOW_DAYS = 28
MIN_HOURLY_READINGS = 30

AREA = "WECA"
TARGET_PCT = 95
TARGET_YEAR = 2030
ON_TIME_BAND = "1 minute early to 5 min 59s late (DfT statistical definition)"

OVERALL_COLS = [
    "on_time", "early", "late", "on_time_pct", "mean_delay_s", "median_delay_s",
    "readings_in_gate", "readings_total", "excluded_distance",
    "median_gate_dist_m", "expected_trips", "observed_trips", "coverage_pct",
]
ROUTE_COLS = [
    "route", "on_time_pct", "mean_delay_s", "median_delay_s",
    "readings_in_gate", "on_time", "early", "late",
    "expected_trips", "observed_trips", "coverage_pct",
]
COVERAGE_COLS = ("expected_trips", "observed_trips", "coverage_pct")

# Display order: network first, then the show-list operators.
OPERATOR_ORDER = [NETWORK_LABEL] + SHOW_OPERATORS


def operators_for_day(cur, service_date):
    present = {r[0] for r in cur.execute(
        "SELECT operator FROM daily_overall_summary WHERE service_date = ?", (service_date,))}
    return [op for op in OPERATOR_ORDER if op in present]


def coverage_is_valid(cur, service_date):
    """Publish coverage only with source provenance and a valid health row."""
    if denominator_reasons(cur.connection, service_date):
        return False
    table = cur.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = 'daily_trip_coverage_days'"""
    ).fetchone()
    if not table:
        return False
    row = cur.execute(
        """SELECT is_valid FROM daily_trip_coverage_days
           WHERE service_date = ?""",
        (service_date,),
    ).fetchone()
    return bool(row and row[0])


def withhold_coverage(row):
    for column in COVERAGE_COLS:
        row[column] = None


def build_operator(cur, service_date, operator, *, coverage_valid=True):
    cur.execute(
        "SELECT * FROM daily_overall_summary WHERE service_date = ? AND operator = ?",
        (service_date, operator),
    )
    row = cur.fetchone()
    overall = {col: row[col] for col in OVERALL_COLS}
    cur.execute(
        """SELECT * FROM daily_route_summary
           WHERE service_date = ? AND operator = ? AND route IS NOT NULL
           ORDER BY readings_in_gate DESC""",
        (service_date, operator),
    )
    routes = [{col: r[col] for col in ROUTE_COLS} for r in cur.fetchall()]
    if not coverage_valid:
        withhold_coverage(overall)
        for route in routes:
            withhold_coverage(route)
    # Routes withheld on their own (identical twin schedules): their coverage
    # is blank and their trips are outside this operator's coverage totals.
    pooled = operator == NETWORK_LABEL
    withheld_labels = {
        public_route(op, route or None, pooled): reasons
        for (op, route), reasons in withheld_routes(cur.connection, service_date).items()
        if pooled or op == operator
    }
    for route in routes:
        route["coverage_withheld"] = withheld_labels.get(route["route"]) or None
        if route["coverage_withheld"]:
            withhold_coverage(route)

    try:
        freq = {
            row["route"]: (bool(row["frequent"]) if row['frequent'] is not None else None)
            for row in cur.execute(
                "SELECT route, frequent FROM daily_route_class WHERE service_date = ? AND operator = ?",
                (service_date, operator),
            )
        }
        for r in routes:
            r["frequent"] = freq.get(r["route"]) if coverage_valid else None
    except sqlite3.OperationalError:
        pass

    geography = {"area": [], "ward": []}
    try:
        cur.execute(
            """SELECT geo_type, geo_key, readings_in_gate, on_time_pct,
                      mean_delay_s, median_delay_s
               FROM daily_geo_summary WHERE service_date = ? AND operator = ?
               ORDER BY readings_in_gate DESC""",
            (service_date, operator),
        )
        for r in cur.fetchall():
            if r["geo_type"] in geography:
                geography[r["geo_type"]].append({
                    "key": r["geo_key"],
                    "on_time_pct": r["on_time_pct"],
                    "readings_in_gate": r["readings_in_gate"],
                    "median_delay_s": r["median_delay_s"],
                    "mean_delay_s": r["mean_delay_s"],
                })
    except sqlite3.OperationalError:
        pass

    fleet = []
    try:
        cur.execute(
            """SELECT model, electric, fuel, vehicles, readings_in_gate,
                      on_time, on_time_pct, mean_delay_s, median_delay_s, routes_json
               FROM daily_fleet_summary WHERE service_date = ? AND operator = ?
               ORDER BY readings_in_gate DESC""",
            (service_date, operator),
        )
        for r in cur.fetchall():
            try:
                model_routes = json.loads(r["routes_json"]) if r["routes_json"] else []
            except (ValueError, TypeError):
                model_routes = []
            fleet.append({
                "model": r["model"],
                "electric": bool(r["electric"]),
                "fuel": r["fuel"],
                "vehicles": r["vehicles"],
                "readings_in_gate": r["readings_in_gate"],
                "on_time": r["on_time"],
                "on_time_pct": r["on_time_pct"],
                "median_delay_s": r["median_delay_s"],
                "routes": model_routes,
            })
    except sqlite3.OperationalError:
        pass

    qualify_row(cur.connection,[service_date],operator,overall,
                coverage_verified=coverage_valid)
    for item in routes:
        qualify_row(cur.connection,[service_date],operator,item,'route',item['route'],
                    coverage_verified=coverage_valid and not item['coverage_withheld'])
    for scope,items in geography.items():
        for item in items:
            qualify_row(cur.connection,[service_date],operator,item,scope,item['key'],
                        coverage_verified=coverage_valid)
    for item in fleet:
        qualify_row(cur.connection,[service_date],operator,item,'fleet',item['model'],
                    coverage_verified=coverage_valid)
    return {"overall": overall, "routes": routes, "geography": geography, "fleet": fleet,
            "coverage_withheld_routes": sorted(withheld_labels),
            "frequency_adherence": frequency_adherence(routes)}


def build_day(cur, service_date):
    ops = operators_for_day(cur, service_date)
    coverage_valid = coverage_is_valid(cur, service_date)
    by_operator = {
        op: build_operator(
            cur, service_date, op, coverage_valid=coverage_valid)
        for op in ops
    }
    if NETWORK_LABEL in by_operator:
        by_operator[NETWORK_LABEL]['frequency_adherence'] = frequency_adherence(
            [row for op,values in by_operator.items() if op != NETWORK_LABEL
             for row in values['routes']])
    day = {"service_date": service_date, "by_operator": by_operator,
           "denominator_reasons": denominator_reasons(cur.connection, service_date),
           "withheld_routes": [
               {"operator": op, "operator_name": operator_name(op),
                "route": route, "reasons": reasons}
               for (op, route), reasons in withheld_routes(cur.connection, service_date).items()]}
    day['operator_composition'] = operator_composition({
        op: values['overall']['readings_in_gate'] for op, values in by_operator.items()
        if op != NETWORK_LABEL})
    # Retain the top-level network keys for existing readers.
    compat = by_operator.get("FBRI") or by_operator.get(NETWORK_LABEL)
    if compat:
        day["overall"] = compat["overall"]
        day["routes"] = compat["routes"]
    return day


def day_type(service_date):
    """Weekday or weekend by the service day the journey belongs to."""
    return "weekend" if datetime.strptime(service_date, "%Y%m%d").weekday() >= 5 else "weekday"


def build_hourly(cur, dates, operator):
    """On-time, early and late counts by local hour, split weekday/weekend.

    Hours are sparse ({"7": [on_time, early, late]}); an hour with no readings
    is left out rather than shown as zero.
    """
    window = dates[-HOURLY_WINDOW_DAYS:]
    result = {
        "operator": operator,
        "window": {
            "from": window[0], "to": window[-1],
            "days": {kind: sum(day_type(d) == kind for d in window)
                     for kind in ("weekday", "weekend")},
        },
        "min_readings": MIN_HOURLY_READINGS,
        "network": {"weekday": {}, "weekend": {}},
        "routes": {},
    }
    placeholders = ",".join("?" for _ in window)
    try:
        rows = cur.execute(
            f"""SELECT service_date, route, hour, on_time, early, late
                  FROM daily_hour_summary
                 WHERE operator = ? AND service_date IN ({placeholders})""",
            (operator, *window)).fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    for row in rows:
        if row["route"] is None:
            target = result["network"]
        else:
            target = result["routes"].setdefault(
                row["route"], {"weekday": {}, "weekend": {}})
        cell = target[day_type(row["service_date"])].setdefault(
            str(row["hour"]), [0, 0, 0])
        cell[0] += row["on_time"]
        cell[1] += row["early"]
        cell[2] += row["late"]
    return result


JOURNEY_BANDS = ("am_peak", "interpeak", "pm_peak", "evening")


def build_journeys(cur, window, operator):
    """Timetabled, seen and very-likely-not-run journeys over the window.

    Counts come only from days whose trip coverage passed its health check;
    routes withheld for duplicate timetable entries are already left out of
    daily_trip_coverage. "Very likely didn't run" counts only from operator
    days whose duty-gap check passed. Each entry is [timetabled, seen,
    very_likely_not_run], in total and by the band the journey starts in.
    """
    from audit_rollup import trip_time_band_for
    pooled = operator == NETWORK_LABEL
    ops = SHOW_OPERATORS if pooled else [operator]
    placeholders = ",".join("?" for _ in window)
    op_ph = ",".join("?" for _ in ops)
    try:
        coverage = cur.execute(
            f"""SELECT operator, route, time_band,
                       SUM(scheduled_trips) AS scheduled, SUM(observed_trips) AS seen
                  FROM valid_daily_trip_coverage
                 WHERE service_date IN ({placeholders}) AND operator IN ({op_ph})
                 GROUP BY operator, route, time_band""", (*window, *ops)).fetchall()
        checked = {(r["operator"], r["service_date"]) for r in cur.execute(
            f"""SELECT operator, service_date FROM valid_daily_duty_gap_days
                 WHERE service_date IN ({placeholders}) AND operator IN ({op_ph})""",
            (*window, *ops))}
        gaps = cur.execute(
            f"""SELECT operator, service_date, route, first_departure
                  FROM valid_daily_duty_gap_candidates
                 WHERE service_date IN ({placeholders}) AND operator IN ({op_ph})""",
            (*window, *ops)).fetchall()
    except sqlite3.OperationalError:
        return None
    if not coverage:
        return None

    def blank():
        return {"total": [0, 0, 0], "bands": {band: [0, 0, 0] for band in JOURNEY_BANDS}}

    result = {"duty_checked": bool(checked), "network": blank(), "routes": {}}

    def add(route, band, index, value):
        for target in (result["network"], result["routes"].setdefault(route, blank())):
            target["total"][index] += value
            if band in target["bands"]:
                target["bands"][band][index] += value

    for row in coverage:
        route = public_route(row["operator"], row["route"], pooled)
        add(route, row["time_band"], 0, int(row["scheduled"] or 0))
        add(route, row["time_band"], 1, int(row["seen"] or 0))
    for row in gaps:
        if (row["operator"], row["service_date"]) in checked:
            add(public_route(row["operator"], row["route"], pooled),
                trip_time_band_for(row["first_departure"]), 2, 1)
    return result


def write_hourly(cur, dates, operators):
    """Write hourly/<operator>.json; return the index for audit_data.json."""
    os.makedirs(os.path.join(OUT_DIR, HOURLY_DIR), exist_ok=True)
    files = {}
    for operator in operators:
        payload = build_hourly(cur, dates, operator)
        if payload is None:
            continue
        journeys = build_journeys(cur, dates[-HOURLY_WINDOW_DAYS:], operator)
        if journeys:
            payload["journeys"] = journeys
        name = f"{HOURLY_DIR}/{operator}.json"
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as out:
            json.dump(payload, out, separators=(",", ":"))
        files[operator] = name
    if not files:
        return None
    return {"window_days": HOURLY_WINDOW_DAYS,
            "min_readings": MIN_HOURLY_READINGS, "files": files}


def main():
    if not os.path.exists(AUDIT_DB):
        print(f"audit.db not found at {AUDIT_DB}")
        return 1

    conn = sqlite3.connect(AUDIT_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT DISTINCT service_date FROM daily_overall_summary ORDER BY service_date")
    except sqlite3.OperationalError:
        print("No rollup tables yet, run: python audit_rollup.py")
        return 1

    candidate_dates = [row[0] for row in cur.fetchall()]
    if not candidate_dates:
        print("No rollup rows yet.")
        return 1

    exclusions = publication_exclusions(conn, candidate_dates)
    dates = [value for value in candidate_dates if value not in exclusions]
    if not dates:
        print("No publication-safe rollup days remain.")
        return 1

    days = [build_day(cur, sd) for sd in dates]

    present = set()
    for day in days:
        present.update(day["by_operator"].keys())
    operators = [
        {"code": op, "name": operator_name(op)}
        for op in OPERATOR_ORDER if op in present
    ]

    payload = {
        "area": AREA,
        "operator": "FBRI",
        "operator_name": "First Bristol",
        "operators": operators,
        "target_pct": TARGET_PCT,
        "target_year": TARGET_YEAR,
        **target_metadata(dates[-1]),
        "on_time_band": ON_TIME_BAND,
        "measurement_method": {
            "version": 3,
            "origin_timing_points": "excluded_until_departure_can_be_detected",
            "sample_qualification": "journey_clusters_with_known_order_sensitivity",
            "historical_snapshot_provenance": "unavailable_unless_retained_at_capture",
            "exact_match_runtime_state": "not_retained_in_this_export",
            "frequency_classification": "registered_route_direction_peak_hour_proxy_v2",
        },
        "excluded_service_days": [
            {"service_date": service_date, "reasons": exclusions[service_date]}
            for service_date in sorted(exclusions)
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    hourly = write_hourly(cur, dates, [o["code"] for o in operators])
    if hourly:
        payload["hourly"] = hourly
    with open(OUT_FILE, "w", encoding="utf-8") as out:
        json.dump(payload, out, separators=(',', ':'))
    exclusion_text = (
        f", excluded: {len(exclusions)}" if exclusions else "")
    print(
        f"Wrote {OUT_FILE}  ({len(dates)} day(s){exclusion_text}, "
        f"operators: {', '.join(o['code'] for o in operators)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
