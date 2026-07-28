#!/usr/bin/env python3
"""Build renderer input from published audit data and successful bot posts."""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path


DELAY_BIN_EDGES_S = (
    -600, -300, -180, -120, -60, 0, 60, 120, 180,
    240, 300, 360, 480, 600, 900, 1200,
)


def _service_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)


def _operator_name(audit: dict, operator: str) -> str:
    for item in audit.get("operators") or []:
        if item.get("code") == operator and item.get("name"):
            return str(item["name"])
    return "WECA network" if operator == "ALL" else operator


def build_week(audit: dict, operator: str | None = None) -> dict:
    operator = operator or audit.get("operator") or "ALL"
    days = sorted(audit.get("days") or [], key=lambda day: day.get("service_date", ""))
    if len(days) < 7:
        raise ValueError("Bus Week requires seven published daily rollups")
    days = days[-7:]
    parsed = [_service_date(day["service_date"]) for day in days]
    if any(current - previous != timedelta(days=1)
           for previous, current in zip(parsed, parsed[1:])):
        raise ValueError("the latest seven audit rollups are not consecutive days")

    overall = []
    for day in days:
        operator_rollup = (day.get("by_operator") or {}).get(operator)
        values = (operator_rollup or {}).get("overall") if operator_rollup else None
        if not isinstance(values, dict):
            raise ValueError(
                f"{day['service_date']} has no {operator} operator rollup")
        if any(values.get(field) is None for field in
               ("readings_in_gate", "on_time", "on_time_pct")):
            raise ValueError(f"{day['service_date']} lacks exact weekly count fields")
        overall.append(values)

    readings = sum(int(item["readings_in_gate"]) for item in overall)
    on_time = sum(int(item["on_time"]) for item in overall)
    if readings < 1000:
        raise ValueError("Bus Week requires at least 1,000 timing-point readings")
    target_pct = float(audit.get("current_target_pct", 82))
    long_term_target_pct = float(audit.get("target_pct", 95))
    target_year = int(audit.get("target_year", 2030))
    on_time_pct = round(100 * on_time / readings, 1)
    return {
        "operatorCode": operator,
        "operatorName": _operator_name(audit, operator),
        "startDate": parsed[0].date().isoformat(),
        "endDate": parsed[-1].date().isoformat(),
        "onTimePct": on_time_pct,
        "onTimeReadings": on_time,
        "readings": readings,
        "serviceDays": 7,
        "daily": [float(item["on_time_pct"]) for item in overall],
        "targetPct": target_pct,
        "targetLabel": "latest WECA area target",
        "targetGapPoints": round(target_pct - on_time_pct, 1),
        "longTermTargetPct": long_term_target_pct,
        "longTermTargetLabel": f"WECA {target_year} goal",
        "longTermTargetGapPoints": round(
            long_term_target_pct - on_time_pct, 1),
    }


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        raise ValueError("cannot calculate a percentile without readings")
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return int(values[lower])
    fraction = position - lower
    return int(round(values[lower] + (values[upper] - values[lower]) * fraction))


def build_distribution(conn: sqlite3.Connection, audit: dict,
                       week: dict) -> dict:
    start_key = week["startDate"].replace("-", "")
    end_key = week["endDate"].replace("-", "")
    selected_operator = week.get("operatorCode") or "ALL"
    operators = ([selected_operator] if selected_operator != "ALL" else
                 sorted({
                     operator
                     for day in audit.get("days") or []
                     if start_key <= str(day.get("service_date") or "") <= end_key
                     for operator in (day.get("by_operator") or {})
                     if operator != "ALL"
                 }))
    if not operators:
        raise ValueError("weekly delay distribution has no operators")
    placeholders = ",".join("?" for _ in operators)
    rows = conn.execute(
        f"""SELECT observed_delay_s
              FROM timepoint_observations
             WHERE service_date BETWEEN ? AND ?
               AND operator IN ({placeholders})
               AND observed_delay_s IS NOT NULL
               AND gps_distance_m IS NOT NULL AND gps_distance_m <= 150""",
        (start_key, end_key, *operators),
    ).fetchall()
    delays = sorted(int(row[0]) for row in rows)
    if len(delays) != week["readings"]:
        raise ValueError(
            "weekly histogram/raw reading mismatch: "
            f"{len(delays)} != {week['readings']}")
    on_time = sum(-60 <= value <= 359 for value in delays)
    if on_time != week["onTimeReadings"]:
        raise ValueError(
            "weekly histogram/on-time mismatch: "
            f"{on_time} != {week['onTimeReadings']}")
    counts = [0] * (len(DELAY_BIN_EDGES_S) + 1)
    for delay in delays:
        counts[bisect.bisect_right(DELAY_BIN_EDGES_S, delay)] += 1
    return {
        "binEdgesSeconds": list(DELAY_BIN_EDGES_S),
        "counts": counts,
        "medianDelaySeconds": int(round(statistics.median(delays))),
        "p10DelaySeconds": _percentile(delays, .10),
        "p90DelaySeconds": _percentile(delays, .90),
    }


def _recent_departures(conn: sqlite3.Connection | None, post: dict,
                       limit: int = 20) -> list[dict]:
    current = {
        "delaySeconds": int(post.get("delaySeconds") or 0),
        "isCurrent": True,
    }
    stop_code = post.get("stopCode")
    if conn is None or not stop_code:
        return [current]
    rows = conn.execute(
        """SELECT observed_delay_s, siri_journey_ref, vehicle_ref
             FROM timepoint_observations
            WHERE stop_code = ?
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= 150
              AND (recorded_at IS NULL OR julianday(recorded_at) <= julianday(?))
            ORDER BY recorded_at DESC
            LIMIT ?""",
        (stop_code, post["eventTimestamp"], limit),
    ).fetchall()
    values = []
    matched = False
    for delay_seconds, journey_ref, vehicle_ref in reversed(rows):
        is_current = bool(
            not matched
            and journey_ref == post.get("journeyRef")
            and vehicle_ref == post.get("vehicleRef")
        )
        matched = matched or is_current
        values.append({
            "delaySeconds": int(delay_seconds),
            **({"isCurrent": True} if is_current else {}),
        })
    if not matched:
        values.append(current)
    return values[-limit:]


def build_bot_said(recent: dict,
                   audit_conn: sqlite3.Connection | None = None) -> dict:
    posts = recent.get("posts") if isinstance(recent, dict) else None
    if not isinstance(posts, list):
        raise ValueError("recent-post input has no posts list")
    for post in posts:
        required = ("postText", "postUrl", "line", "eventTimestamp",
                    "operatorRef", "vehicleRef", "journeyRef")
        if not isinstance(post, dict) or not all(post.get(key) for key in required):
            continue
        stop = post.get("stopName") or post.get("stopCode")
        if not stop:
            continue
        seconds = int(post.get("delaySeconds") or 0)
        result = {
            "postText": str(post["postText"]),
            "postUrl": str(post["postUrl"]),
            "route": str(post["line"]),
            "stop": str(stop),
            "observedAt": str(post["eventTimestamp"]),
            # Match JavaScript Math.round for signed values.
            "delayMinutes": math.floor(seconds / 60 + 0.5),
            "vehicleRef": str(post["vehicleRef"]),
            "operatorRef": str(post["operatorRef"]),
            "journeyRef": str(post["journeyRef"]),
            "stopCode": str(post.get("stopCode") or ""),
        }
        result["recentDepartures"] = _recent_departures(
            audit_conn, post)
        return result
    raise ValueError("no recent post has complete exact-journey and stop provenance")


def build_pack(audit: dict, recent: dict,
               now: datetime | None = None,
               audit_conn: sqlite3.Connection | None = None,
               operator: str | None = None) -> dict:
    week = build_week(audit, operator)
    if audit_conn is not None:
        week["distribution"] = build_distribution(audit_conn, audit, week)
    return {
        "generatedAt": (now or datetime.now(timezone.utc)).isoformat(),
        "botSaid": build_bot_said(recent, audit_conn),
        "busWeek": week,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-json", type=Path, required=True)
    parser.add_argument("--recent-posts-json", type=Path, required=True)
    parser.add_argument("--audit-db", type=Path)
    parser.add_argument(
        "--operator",
        help="operator code for weekly cards (defaults to audit JSON selection)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    audit = json.loads(args.audit_json.read_text(encoding="utf-8"))
    recent = json.loads(args.recent_posts_json.read_text(encoding="utf-8"))
    if args.audit_db and not args.audit_db.is_file():
        parser.error(f"audit database not found: {args.audit_db}")
    conn = sqlite3.connect(
        f"{args.audit_db.resolve().as_uri()}?mode=ro", uri=True
    ) if args.audit_db else None
    try:
        pack = build_pack(
            audit, recent, audit_conn=conn, operator=args.operator)
    finally:
        if conn is not None:
            conn.close()
    args.output.write_text(
        json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
