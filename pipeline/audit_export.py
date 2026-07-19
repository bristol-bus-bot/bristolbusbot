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

from audit_operators import SHOW_OPERATORS, NETWORK_LABEL, operator_name

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DB = os.getenv("BBB_AUDIT_DB", os.path.join(HERE, "audit.db"))
OUT_DIR = os.getenv("BBB_AUDIT_SITE_DIR", os.path.join(HERE, "audit_site"))
OUT_FILE = os.path.join(OUT_DIR, "audit_data.json")

AREA = "WECA"
TARGET_PCT = 95
ON_TIME_BAND = "1 minute early to 5 min 59s late (DfT statistical definition)"

OVERALL_COLS = [
    "on_time_pct", "mean_delay_s", "median_delay_s",
    "readings_in_gate", "readings_total", "excluded_distance",
    "median_gate_dist_m", "expected_trips", "observed_trips", "coverage_pct",
]
ROUTE_COLS = [
    "route", "on_time_pct", "mean_delay_s", "median_delay_s",
    "readings_in_gate", "on_time", "early", "late",
    "expected_trips", "observed_trips", "coverage_pct",
]

# Display order: network first, then the show-list operators.
OPERATOR_ORDER = [NETWORK_LABEL] + SHOW_OPERATORS


def operators_for_day(cur, service_date):
    present = {r[0] for r in cur.execute(
        "SELECT operator FROM daily_overall_summary WHERE service_date = ?", (service_date,))}
    return [op for op in OPERATOR_ORDER if op in present]


def build_operator(cur, service_date, operator):
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

    try:
        freq = {
            row["route"]: bool(row["frequent"])
            for row in cur.execute(
                "SELECT route, frequent FROM daily_route_class WHERE service_date = ? AND operator = ?",
                (service_date, operator),
            )
        }
        for r in routes:
            r["frequent"] = freq.get(r["route"], False)
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
                      on_time_pct, mean_delay_s, median_delay_s, routes_json
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
                "on_time_pct": r["on_time_pct"],
                "median_delay_s": r["median_delay_s"],
                "routes": model_routes,
            })
    except sqlite3.OperationalError:
        pass

    return {"overall": overall, "routes": routes, "geography": geography, "fleet": fleet}


def build_day(cur, service_date):
    ops = operators_for_day(cur, service_date)
    by_operator = {op: build_operator(cur, service_date, op) for op in ops}
    day = {"service_date": service_date, "by_operator": by_operator}
    # Retain the top-level network keys for existing readers.
    compat = by_operator.get("FBRI") or by_operator.get(NETWORK_LABEL)
    if compat:
        day["overall"] = compat["overall"]
        day["routes"] = compat["routes"]
    return day


def main():
    if not os.path.exists(AUDIT_DB):
        print(f"audit.db not found at {AUDIT_DB}")
        return

    conn = sqlite3.connect(AUDIT_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT DISTINCT service_date FROM daily_overall_summary ORDER BY service_date")
    except sqlite3.OperationalError:
        print("No rollup tables yet, run: python audit_rollup.py")
        return

    dates = [row[0] for row in cur.fetchall()]
    if not dates:
        print("No rollup rows yet.")
        return

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
        "on_time_band": ON_TIME_BAND,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2)
    print(f"Wrote {OUT_FILE}  ({len(dates)} day(s), operators: {', '.join(o['code'] for o in operators)})")


if __name__ == "__main__":
    main()
