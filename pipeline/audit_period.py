"""The audit page's default view: every route over the last 28 published days.

One service day leaves out every route that didn't run that day (a Sunday
shows about 75 of 175) and gives most routes too few readings to rely on.
Pooling the last 28 published days fixes both. Figures are summed from the
same daily rollups the day view uses; medians come from the retained raw
readings for exactly those days. Journey coverage counts only days whose
trip coverage passed its health check, and leaves out route-days whose
coverage was withheld.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict

from audit_operators import NETWORK_LABEL, SHOW_OPERATORS, public_route
from audit_scope import frequency_adherence, operator_composition
import route_split
from sample_quality import qualify_row
from snapshot_quality import withheld_routes

PERIOD_DAYS = 28
DISTANCE_GATE_M = 150
OPERATOR_ORDER = [NETWORK_LABEL] + SHOW_OPERATORS


def _placeholders(values):
    return ",".join("?" for _ in values)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def _raw_medians(cur, window):
    """{label: {"overall": median, route: median}} from raw readings."""
    delays = defaultdict(lambda: defaultdict(list))
    try:
        rows = cur.execute(
            f"""SELECT operator, route, observed_delay_s, trip_id,
                       {route_split.column_or_null(cur.connection, "timepoint_observations", "stop_code")}
                  FROM timepoint_observations
                 WHERE service_date IN ({_placeholders(window)})
                   AND operator IN ({_placeholders(SHOW_OPERATORS)})
                   AND COALESCE(is_origin, 0) = 0
                   AND observed_delay_s IS NOT NULL
                   AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
            (*window, *SHOW_OPERATORS, DISTANCE_GATE_M))
    except sqlite3.OperationalError:
        return {}
    split = route_split.for_connection(cur.connection)
    for operator, route, delay, trip_id, stop_code in rows:
        route = split.route(operator, route, trip_id, stop_code)
        for label, pooled in ((operator, False), (NETWORK_LABEL, True)):
            delays[label][None].append(delay)
            if route is not None:
                delays[label][public_route(operator, route, pooled)].append(delay)
    return {label: {key: int(round(statistics.median(values)))
                    for key, values in by_key.items() if values}
            for label, by_key in delays.items()}


def _valid_days(cur, window, coverage_is_valid):
    return {day for day in window if coverage_is_valid(cur, day)}


def _overall(cur, window, operator, valid, medians):
    rows = cur.execute(
        f"""SELECT * FROM daily_overall_summary
             WHERE operator = ? AND service_date IN ({_placeholders(window)})""",
        (operator, *window)).fetchall()
    if not rows:
        return None
    total = defaultdict(int)
    weighted_mean = 0.0
    for r in rows:
        for column in ("on_time", "early", "late", "readings_in_gate", "readings_total", "excluded_distance"):
            total[column] += int(r[column] or 0)
        if r["mean_delay_s"] is not None:
            weighted_mean += r["mean_delay_s"] * int(r["readings_in_gate"] or 0)
        if r["service_date"] in valid and r["expected_trips"] is not None:
            total["expected_trips"] += int(r["expected_trips"] or 0)
            total["observed_trips"] += int(r["observed_trips"] or 0)
            total["coverage_days"] += 1
    in_gate = total["readings_in_gate"]
    covered = total["coverage_days"]
    ran = sorted(r["service_date"] for r in rows if int(r["readings_in_gate"] or 0) > 0)
    return ran, {
        "on_time": total["on_time"], "early": total["early"], "late": total["late"],
        "on_time_pct": _pct(total["on_time"], in_gate),
        "mean_delay_s": int(round(weighted_mean / in_gate)) if in_gate else None,
        "median_delay_s": medians.get(None),
        "readings_in_gate": in_gate, "readings_total": total["readings_total"],
        "excluded_distance": total["excluded_distance"], "median_gate_dist_m": None,
        "expected_trips": total["expected_trips"] if covered else None,
        "observed_trips": total["observed_trips"] if covered else None,
        "coverage_pct": _pct(total["observed_trips"], total["expected_trips"]) if covered else None,
    }


def _routes(cur, window, operator, valid, medians):
    pooled = operator == NETWORK_LABEL
    withheld_by_day = {
        day: {public_route(op, route or None, pooled): reasons
              for (op, route), reasons in withheld_routes(cur.connection, day).items()
              if pooled or op == operator}
        for day in window}
    acc = defaultdict(lambda: defaultdict(float))
    ran_days = defaultdict(set)
    withheld_reasons = {}
    for r in cur.execute(
            f"""SELECT * FROM daily_route_summary
                 WHERE operator = ? AND route IS NOT NULL
                   AND service_date IN ({_placeholders(window)})""",
            (operator, *window)):
        a = acc[r["route"]]
        if int(r["readings_in_gate"] or 0) > 0:
            ran_days[r["route"]].add(r["service_date"])
        for column in ("on_time", "early", "late", "readings_in_gate"):
            a[column] += int(r[column] or 0)
        if r["mean_delay_s"] is not None:
            a["mean_weighted"] += r["mean_delay_s"] * int(r["readings_in_gate"] or 0)
        reasons = withheld_by_day[r["service_date"]].get(r["route"])
        if reasons:
            a["withheld_days"] += 1
            withheld_reasons[r["route"]] = reasons
        elif r["service_date"] in valid and r["expected_trips"] is not None:
            a["expected_trips"] += int(r["expected_trips"] or 0)
            a["observed_trips"] += int(r["observed_trips"] or 0)
            a["coverage_days"] += 1
        a["days"] += 1
    frequent = {}
    try:
        for r in cur.execute(
                f"""SELECT route, frequent FROM daily_route_class
                     WHERE operator = ? AND service_date IN ({_placeholders(window)})
                     ORDER BY service_date""",
                (operator, *window)):
            if r["frequent"] is not None:
                frequent[r["route"]] = bool(r["frequent"])
    except sqlite3.OperationalError:
        pass
    routes = []
    for route, a in acc.items():
        in_gate = int(a["readings_in_gate"])
        # Mostly withheld: coverage is too uncertain to show at all.
        withheld = withheld_reasons.get(route) if a["withheld_days"] * 2 > a["days"] else None
        covered = a["coverage_days"] and not withheld
        routes.append({
            "route": route,
            "on_time_pct": _pct(a["on_time"], in_gate),
            "mean_delay_s": int(round(a["mean_weighted"] / in_gate)) if in_gate else None,
            "median_delay_s": medians.get(route),
            "readings_in_gate": in_gate, "on_time": int(a["on_time"]),
            "early": int(a["early"]), "late": int(a["late"]),
            "expected_trips": int(a["expected_trips"]) if covered else None,
            "observed_trips": int(a["observed_trips"]) if covered else None,
            "coverage_pct": _pct(a["observed_trips"], a["expected_trips"]) if covered else None,
            "coverage_withheld": withheld,
            "frequent": frequent.get(route),
            "service_days": int(a["days"]),
            "_ran": sorted(ran_days[route]),
        })
    routes.sort(key=lambda r: -r["readings_in_gate"])
    return routes, sorted(r["route"] for r in routes if r["coverage_withheld"])


def _geography(cur, window, operator):
    geography = {"area": [], "ward": []}
    try:
        rows = cur.execute(
            f"""SELECT geo_type, geo_key, SUM(readings_in_gate) AS readings,
                       SUM(on_time) AS on_time,
                       GROUP_CONCAT(CASE WHEN readings_in_gate > 0 THEN service_date END) AS ran
                  FROM daily_geo_summary
                 WHERE operator = ? AND service_date IN ({_placeholders(window)})
                 GROUP BY geo_type, geo_key ORDER BY readings DESC""",
            (operator, *window)).fetchall()
    except sqlite3.OperationalError:
        return geography
    for r in rows:
        if r["geo_type"] in geography:
            geography[r["geo_type"]].append({
                "key": r["geo_key"], "readings_in_gate": int(r["readings"] or 0),
                "on_time": int(r["on_time"] or 0),
                "on_time_pct": _pct(int(r["on_time"] or 0), int(r["readings"] or 0)),
                "median_delay_s": None, "mean_delay_s": None,
                "_ran": sorted(set((r["ran"] or "").split(",")) - {""}),
            })
    return geography


def _fleet(cur, window, operator):
    models = {}
    try:
        rows = cur.execute(
            f"""SELECT * FROM daily_fleet_summary
                 WHERE operator = ? AND service_date IN ({_placeholders(window)})""",
            (operator, *window)).fetchall()
    except sqlite3.OperationalError:
        return []
    for r in rows:
        m = models.setdefault(r["model"], {
            "model": r["model"], "electric": bool(r["electric"]), "fuel": r["fuel"],
            "vehicles": 0, "readings_in_gate": 0, "on_time": 0, "routes": set(), "_ran": set()})
        if int(r["readings_in_gate"] or 0) > 0:
            m["_ran"].add(r["service_date"])
        m["vehicles"] = max(m["vehicles"], int(r["vehicles"] or 0))
        m["readings_in_gate"] += int(r["readings_in_gate"] or 0)
        m["on_time"] += int(r["on_time"] or 0)
        try:
            m["routes"].update(json.loads(r["routes_json"]) if r["routes_json"] else [])
        except (ValueError, TypeError):
            pass
    fleet = []
    for m in models.values():
        fleet.append({**m, "routes": sorted(m["routes"], key=str), "_ran": sorted(m["_ran"]),
                      "on_time_pct": _pct(m["on_time"], m["readings_in_gate"]),
                      "median_delay_s": None})
    fleet.sort(key=lambda m: -m["readings_in_gate"])
    return fleet


def build_period(cur, dates, coverage_is_valid):
    """Pooled view of the last PERIOD_DAYS published days, or None."""
    window = list(dates[-PERIOD_DAYS:])
    if not window:
        return None
    valid = _valid_days(cur, window, coverage_is_valid)
    medians = _raw_medians(cur, window)
    by_operator = {}
    for operator in OPERATOR_ORDER:
        found = _overall(cur, window, operator, valid, medians.get(operator, {}))
        if found is None:
            continue
        ran, overall = found
        routes, withheld = _routes(cur, window, operator, valid, medians.get(operator, {}))
        geography = _geography(cur, window, operator)
        fleet = _fleet(cur, window, operator)
        # Sample support exists only for days a route (or place, or model)
        # actually ran, so each is qualified over its own days in the window.
        conn = cur.connection
        qualify_row(conn, ran, operator, overall, coverage_verified=bool(valid))
        for item in routes:
            qualify_row(conn, item.pop("_ran"), operator, item, "route", item["route"],
                        coverage_verified=bool(valid) and not item["coverage_withheld"])
        for scope, items in geography.items():
            for item in items:
                qualify_row(conn, item.pop("_ran"), operator, item, scope, item["key"],
                            coverage_verified=bool(valid))
        for item in fleet:
            qualify_row(conn, item.pop("_ran"), operator, item, "fleet", item["model"],
                        coverage_verified=bool(valid))
        by_operator[operator] = {
            "overall": overall, "routes": routes, "geography": geography, "fleet": fleet,
            "coverage_withheld_routes": withheld,
            "frequency_adherence": frequency_adherence(routes),
        }
    if not by_operator:
        return None
    return {
        "from": window[0], "to": window[-1], "days": len(window),
        "coverage_days": len(valid),
        "by_operator": by_operator,
        "operator_composition": operator_composition({
            op: values["overall"]["readings_in_gate"] for op, values in by_operator.items()
            if op != NETWORK_LABEL}),
    }
