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

DEFAULT_OPERATOR_NAMES = {
    "FBRI": "First Bristol",
    "SCGL": "Stagecoach West",
}

DELAY_BUCKETS = (
    "early_5plus",
    "early_1_5",
    "on_time",
    "late_6_10",
    "late_10_20",
    "late_20plus",
)

POST_PROVENANCE_COLUMNS = {
    "id", "operator_ref", "vehicle_ref", "line", "journey_ref",
    "event_timestamp", "delay_seconds", "stop_code", "stop_name",
    "post_uri", "post_content", "low_confidence",
}


def _service_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)


def _operator_name(audit: dict, operator: str) -> str:
    for item in audit.get("operators") or []:
        if item.get("code") == operator and item.get("name"):
            return str(item["name"])
    return "WECA network" if operator == "ALL" else operator


def _operator_names(audit: dict) -> dict[str, str]:
    return {
        str(item["code"]): str(item["name"])
        for item in audit.get("operators") or []
        if item.get("code") and item.get("name")
    }


def _operator_comparison(audit: dict, days: list[dict]) -> list[dict]:
    comparison = []
    for item in audit.get("operators") or []:
        operator = str(item.get("code") or "")
        if not operator or operator == "ALL":
            continue
        readings = on_time = 0
        for day in days:
            overall = (((day.get("by_operator") or {}).get(operator) or {})
                       .get("overall") or {})
            readings += int(overall.get("readings_in_gate") or 0)
            on_time += int(overall.get("on_time") or 0)
        if readings:
            comparison.append({
                "operatorCode": operator,
                "operatorName": str(item.get("name") or operator),
                "readings": readings,
                "onTime": on_time,
                "onTimePct": round(100 * on_time / readings, 1),
            })
    return comparison


def _powertrain_summary(days: list[dict], operator: str,
                        total_readings: int) -> dict:
    groups = {
        "electric": {"readings": 0, "onTime": 0},
        "dieselOther": {"readings": 0, "onTime": 0},
    }
    for day in days:
        fleet = ((day.get("by_operator") or {}).get(operator) or {}).get(
            "fleet") or []
        for row in fleet:
            readings = int(row.get("readings_in_gate") or 0)
            if readings <= 0:
                continue
            group = groups["electric" if row.get("electric") else
                           "dieselOther"]
            on_time = row.get("on_time")
            if on_time is None:
                on_time = round(
                    readings * float(row.get("on_time_pct") or 0) / 100)
            group["readings"] += readings
            group["onTime"] += int(on_time)

    identified = sum(group["readings"] for group in groups.values())
    if identified <= 0:
        raise ValueError("Bus Week has no fleet-matched readings")
    if identified > total_readings:
        raise ValueError(
            "Bus Week fleet readings exceed the operator total: "
            f"{identified} > {total_readings}")
    for group in groups.values():
        readings = group["readings"]
        group["sharePct"] = round(100 * readings / identified, 1)
        group["onTimePct"] = round(
            100 * group["onTime"] / readings, 1) if readings else None
    electric_pct = groups["electric"]["onTimePct"]
    other_pct = groups["dieselOther"]["onTimePct"]
    return {
        "identifiedReadings": identified,
        "unidentifiedReadings": total_readings - identified,
        **groups,
        "onTimeDifferencePoints": round(electric_pct - other_pct, 1),
    }


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
    week = {
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
    week["powertrain"] = _powertrain_summary(days, operator, readings)
    week["operatorComparison"] = _operator_comparison(audit, days)
    return week


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


def _delay_bucket(delay_s: int) -> str:
    if delay_s < -300:
        return "early_5plus"
    if delay_s < -60:
        return "early_1_5"
    if delay_s <= 359:
        return "on_time"
    if delay_s <= 600:
        return "late_6_10"
    if delay_s <= 1200:
        return "late_10_20"
    return "late_20plus"


def _reconcile_frozen_delays(
        conn: sqlite3.Connection, audit: dict, week: dict,
        operators: list[str]) -> tuple[list[int], int]:
    """Match mutable raw rows to the published daily broad histograms.

    A small number of observations can settle after the daily rollup. The
    published daily summaries are the public record, so retain the oldest raw
    rows in each day/bucket and exclude only the newest count surplus. Any
    shortage or inconsistent frozen histogram fails closed.
    """
    histogram_table = conn.execute(
        """SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='daily_delay_histogram'"""
    ).fetchone()
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(timepoint_observations)")
    }
    if histogram_table is None or "recorded_at" not in columns:
        raise ValueError(
            "weekly histogram/raw reading mismatch and frozen reconciliation "
            "data is unavailable")

    start_key = week["startDate"].replace("-", "")
    end_key = week["endDate"].replace("-", "")
    selected_operator = week.get("operatorCode") or "ALL"
    frozen = {
        (str(service_date), str(bucket)): int(count)
        for service_date, bucket, count in conn.execute(
            """SELECT service_date, bucket, SUM(n)
                 FROM daily_delay_histogram
                WHERE service_date BETWEEN ? AND ?
                  AND operator = ? AND route IS NULL
                GROUP BY service_date, bucket""",
            (start_key, end_key, selected_operator),
        )
    }

    published_days = {
        str(day.get("service_date")): int(
            ((((day.get("by_operator") or {}).get(selected_operator) or {})
              .get("overall") or {}).get("readings_in_gate") or 0)
        )
        for day in audit.get("days") or []
        if start_key <= str(day.get("service_date") or "") <= end_key
    }
    expected_dates = [
        (datetime.strptime(start_key, "%Y%m%d") + timedelta(days=index))
        .strftime("%Y%m%d")
        for index in range(7)
    ]
    for service_date in expected_dates:
        published = published_days.get(service_date)
        frozen_total = sum(
            frozen.get((service_date, bucket), 0)
            for bucket in DELAY_BUCKETS)
        if published is None or frozen_total != published:
            raise ValueError(
                "weekly frozen histogram does not match the published daily "
                f"total for {service_date}: {frozen_total} != {published}")

    placeholders = ",".join("?" for _ in operators)
    rows = conn.execute(
        f"""SELECT rowid, service_date, observed_delay_s, recorded_at
              FROM timepoint_observations
             WHERE service_date BETWEEN ? AND ?
               AND operator IN ({placeholders})
               AND observed_delay_s IS NOT NULL
               AND gps_distance_m IS NOT NULL AND gps_distance_m <= 150""",
        (start_key, end_key, *operators),
    ).fetchall()
    grouped: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
    for rowid, service_date, delay_s, recorded_at in rows:
        key = (str(service_date), _delay_bucket(int(delay_s)))
        grouped.setdefault(key, []).append(
            (str(recorded_at or ""), int(rowid), int(delay_s)))

    reconciled: list[int] = []
    excluded = 0
    for service_date in expected_dates:
        for bucket in DELAY_BUCKETS:
            values = sorted(grouped.get((service_date, bucket), []))
            keep = frozen.get((service_date, bucket), 0)
            if len(values) < keep:
                raise ValueError(
                    "weekly raw rows are below the frozen histogram for "
                    f"{service_date}/{bucket}: {len(values)} < {keep}")
            reconciled.extend(value[2] for value in values[:keep])
            excluded += len(values) - keep
    return sorted(reconciled), excluded


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
    excluded_post_rollup = 0
    if len(delays) != week["readings"]:
        delays, excluded_post_rollup = _reconcile_frozen_delays(
            conn, audit, week, operators)
        if len(delays) != week["readings"]:
            raise ValueError(
                "weekly reconciled/raw reading mismatch: "
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
        "postRollupExtrasExcluded": excluded_post_rollup,
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
                   audit_conn: sqlite3.Connection | None = None,
                   operator_names: dict[str, str] | None = None) -> dict:
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
            "operatorName": (operator_names or {}).get(
                str(post["operatorRef"]), str(post["operatorRef"])),
            "journeyRef": str(post["journeyRef"]),
            "stopCode": str(post.get("stopCode") or ""),
        }
        result["recentDepartures"] = _recent_departures(
            audit_conn, post)
        return result
    raise ValueError("no recent post has complete exact-journey and stop provenance")


def read_bot_post(path: Path, post_uri: str, post_url: str,
                  audit_conn: sqlite3.Connection | None = None,
                  operator_names: dict[str, str] | None = None) -> dict:
    """Build one card only from the bot's stored successful-post provenance."""
    if not path.is_file():
        raise FileNotFoundError(path)
    if not post_uri.startswith("at://") or \
            "/app.bsky.feed.post/" not in post_uri:
        raise ValueError("post URI is not a full Bluesky post AT URI")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(engagement_analytics)")
        }
        missing = sorted(POST_PROVENANCE_COLUMNS - columns)
        if missing:
            raise RuntimeError(
                "engagement database predates exact post provenance: "
                + ", ".join(missing))
        row = conn.execute(
            """SELECT operator_ref, vehicle_ref, line, journey_ref,
                      event_timestamp, delay_seconds, stop_code, stop_name,
                      post_uri, post_content, low_confidence
                 FROM engagement_analytics
                WHERE post_uri = ?
                ORDER BY id DESC LIMIT 1""",
            (post_uri,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("the requested post is not in the bot's successful-post log")
    if bool(row["low_confidence"]):
        raise ValueError("the requested post has low-confidence journey provenance")
    payload = {
        "posts": [{
            "postText": row["post_content"],
            "postUrl": post_url,
            "postUri": row["post_uri"],
            "line": row["line"],
            "eventTimestamp": row["event_timestamp"],
            "operatorRef": row["operator_ref"],
            "vehicleRef": row["vehicle_ref"],
            "journeyRef": row["journey_ref"],
            "stopCode": row["stop_code"],
            "stopName": row["stop_name"],
            "delaySeconds": row["delay_seconds"],
        }],
    }
    names = {**DEFAULT_OPERATOR_NAMES, **(operator_names or {})}
    result = build_bot_said(payload, audit_conn, names)
    result["postUri"] = post_uri
    return result


def build_pack(audit: dict, recent: dict,
               now: datetime | None = None,
               audit_conn: sqlite3.Connection | None = None,
               operator: str | None = None) -> dict:
    week = build_week(audit, operator)
    if audit_conn is not None:
        week["distribution"] = build_distribution(audit_conn, audit, week)
    return {
        "generatedAt": (now or datetime.now(timezone.utc)).isoformat(),
        "botSaid": build_bot_said(
            recent, audit_conn, _operator_names(audit)),
        "busWeek": week,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--recent-posts-json", type=Path)
    parser.add_argument("--app-db", type=Path)
    parser.add_argument("--post-uri")
    parser.add_argument("--post-url")
    parser.add_argument("--audit-db", type=Path)
    parser.add_argument(
        "--operator",
        help="operator code for weekly cards (defaults to audit JSON selection)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    single = bool(args.app_db or args.post_uri or args.post_url)
    if single and not all((args.app_db, args.post_uri, args.post_url)):
        parser.error("single-card mode requires --app-db, --post-uri and --post-url")
    if single and (args.audit_json or args.recent_posts_json or args.operator):
        parser.error(
            "single-card mode cannot be combined with weekly pack inputs")
    if not single and not all((args.audit_json, args.recent_posts_json)):
        parser.error(
            "weekly mode requires --audit-json and --recent-posts-json")
    if args.audit_db and not args.audit_db.is_file():
        parser.error(f"audit database not found: {args.audit_db}")
    audit_conn = sqlite3.connect(
        f"{args.audit_db.resolve().as_uri()}?mode=ro", uri=True
    ) if args.audit_db else None
    try:
        if single:
            pack = {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "botSaid": read_bot_post(
                    args.app_db, args.post_uri, args.post_url,
                    audit_conn=audit_conn),
            }
        else:
            audit = json.loads(args.audit_json.read_text(encoding="utf-8"))
            recent = json.loads(
                args.recent_posts_json.read_text(encoding="utf-8"))
            pack = build_pack(
                audit, recent, audit_conn=audit_conn,
                operator=args.operator)
    finally:
        if audit_conn is not None:
            audit_conn.close()
    args.output.write_text(
        json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
