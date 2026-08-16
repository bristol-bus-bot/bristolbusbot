#!/usr/bin/env python3
"""Build a bounded, read-only 48-hour collector anomaly report.

The report never changes collector or timetable data.  It deliberately keeps
only a small evidence sample while retaining the full anomaly counts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


EXTREME_LATE_S = 60 * 60
EXTREME_EARLY_S = -10 * 60
IMPOSSIBLE_SPEED_KPH = 130.0
MIN_SPEED_DISTANCE_M = 1_000.0
MAX_SPEED_INTERVAL_S = 2 * 60 * 60
GPS_NEAR_GATE_M = 900
EVIDENCE_LIMIT = 100


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def require_columns(connection: sqlite3.Connection, table: str,
                    required: set[str]) -> None:
    columns = {row[1] for row in connection.execute(
        f"PRAGMA table_info({table})")}
    missing = required - columns
    if missing:
        raise RuntimeError(
            f"{table} is missing required columns: {', '.join(sorted(missing))}")


def percentile(values: list[int], proportion: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(proportion * len(ordered)) - 1)
    return ordered[index]


def haversine_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (math.sin(dlat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 6_371_000.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _evidence_time(item: dict) -> datetime | None:
    for field in (
        "recorded_at", "current_recorded_at", "overlap_start",
        "from_recorded_at",
    ):
        parsed = parse_time(item.get(field))
        if parsed is not None:
            return parsed
    return None


def _representative_evidence(items: list[dict], limit: int) -> list[dict]:
    """Pick deterministic evidence spread across the whole time window."""
    if len(items) <= limit:
        return items
    ordered = sorted(
        items,
        key=lambda item: (
            _evidence_time(item) or datetime.max.replace(tzinfo=timezone.utc),
            json.dumps(item, sort_keys=True, default=str),
        ),
    )
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    indices = [round(index * (len(ordered) - 1) / (limit - 1))
               for index in range(limit)]
    return [ordered[index] for index in indices]


def _breakdowns(items: list[dict]) -> dict[str, dict[str, int]]:
    result = {}
    for field in ("operator", "route", "stop_sequence", "is_origin",
                  "current_stop_sequence", "direction"):
        counts = Counter(
            str(item[field]) for item in items
            if item.get(field) is not None and str(item[field]).strip()
        )
        if counts:
            result[field] = dict(sorted(
                counts.items(), key=lambda pair: (-pair[1], pair[0])))
    return result


def bounded(items: Iterable[dict], limit: int = EVIDENCE_LIMIT) -> dict:
    materialised = list(items)
    return {
        "count": len(materialised),
        "breakdowns": _breakdowns(materialised),
        "evidence": _representative_evidence(materialised, limit),
        "evidence_limit": limit,
        "evidence_selection": "evenly_spaced_across_time",
        "evidence_truncated": len(materialised) > limit,
    }


def observation_detail(row: dict) -> dict:
    return {
        "service_date": row["service_date"],
        "operator": row["operator"],
        "route": row["route"],
        "trip_id": row["trip_id"],
        "vehicle_ref": row["vehicle_ref"],
        "stop_sequence": row["stop_sequence"],
        "is_origin": bool(row.get("is_origin")),
        "stop_code": row["stop_code"],
        "scheduled_local": row["scheduled_local"],
        "recorded_at": row["recorded_at"],
    }


def poll_metrics(rows: list[dict]) -> dict:
    totals = {
        field: sum(int(row.get(field) or 0) for row in rows)
        for field in (
            "vehicles_total", "candidates", "matched", "obs_written",
            "dropped_insane", "stale")
    }
    totals["polls"] = len(rows)
    totals["successful_polls"] = sum(bool(row.get("ok")) for row in rows)
    totals["rejected_readings"] = max(
        0, totals["candidates"] - totals["matched"])
    totals["match_rate"] = (
        round(totals["matched"] / totals["candidates"], 4)
        if totals["candidates"] else None)
    return totals


def metric_change(older: dict, recent: dict, name: str) -> float | int | None:
    left, right = older.get(name), recent.get(name)
    if left is None or right is None:
        return None
    return round(right - left, 4) if isinstance(left, float) else right - left


def load_stop_coordinates(timetable: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    require_columns(timetable, "stops", {"stop_code", "stop_lat", "stop_lon"})
    coordinates: dict[str, tuple[float, float]] = {}
    for row in timetable.execute(
            """SELECT stop_code, AVG(CAST(stop_lat AS REAL)) AS lat,
                      AVG(CAST(stop_lon AS REAL)) AS lon
                 FROM stops
                WHERE stop_code IS NOT NULL AND TRIM(stop_code) <> ''
                  AND stop_lat IS NOT NULL AND stop_lon IS NOT NULL
                GROUP BY stop_code"""):
        try:
            lat, lon = float(row[1]), float(row[2])
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates[str(row[0])] = (lat, lon)
    return coordinates


def analyse_observations(rows: list[dict],
                         coordinates: dict[str, tuple[float, float]]) -> dict:
    extreme = []
    origin_timing_points = []
    near_gate = []
    for row in rows:
        delay = row.get("observed_delay_s")
        if row.get("is_origin"):
            item = observation_detail(row)
            item["observed_delay_s"] = delay
            origin_timing_points.append(item)
        elif delay is not None and (
                delay >= EXTREME_LATE_S or delay <= EXTREME_EARLY_S):
            item = observation_detail(row)
            item["observed_delay_s"] = delay
            item["direction"] = "late" if delay >= EXTREME_LATE_S else "early"
            extreme.append(item)
        distance = row.get("gps_distance_m")
        if distance is not None and distance >= GPS_NEAR_GATE_M:
            item = observation_detail(row)
            item["gps_distance_m"] = distance
            near_gate.append(item)

    vehicle_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("vehicle_ref") and parse_time(row.get("recorded_at")):
            vehicle_rows[(row["operator"], row["vehicle_ref"])].append(row)

    backwards = []
    speeds = []
    overlaps = []
    coordinate_pairs = 0
    for (operator, vehicle), observations in vehicle_rows.items():
        observations.sort(key=lambda row: parse_time(row["recorded_at"]))
        previous_by_trip: dict[tuple[str, str], dict] = {}
        intervals: dict[tuple[str, str], dict] = {}
        for row in observations:
            trip_key = (row["service_date"], row["trip_id"])
            previous = previous_by_trip.get(trip_key)
            if previous and row["stop_sequence"] < previous["stop_sequence"]:
                backwards.append({
                    "operator": operator,
                    "vehicle_ref": vehicle,
                    "route": row["route"],
                    "service_date": row["service_date"],
                    "trip_id": row["trip_id"],
                    "previous_stop_sequence": previous["stop_sequence"],
                    "current_stop_sequence": row["stop_sequence"],
                    "previous_stop_code": previous["stop_code"],
                    "current_stop_code": row["stop_code"],
                    "previous_recorded_at": previous["recorded_at"],
                    "current_recorded_at": row["recorded_at"],
                })
            previous_by_trip[trip_key] = row

            instant = parse_time(row["recorded_at"])
            interval = intervals.setdefault(trip_key, {
                "service_date": row["service_date"],
                "trip_id": row["trip_id"],
                "route": row["route"],
                "start": instant,
                "end": instant,
            })
            interval["start"] = min(interval["start"], instant)
            interval["end"] = max(interval["end"], instant)

        for left, right in zip(observations, observations[1:]):
            left_time = parse_time(left["recorded_at"])
            right_time = parse_time(right["recorded_at"])
            elapsed = (right_time - left_time).total_seconds()
            left_coordinate = coordinates.get(str(left.get("stop_code") or ""))
            right_coordinate = coordinates.get(str(right.get("stop_code") or ""))
            if (not left_coordinate or not right_coordinate or elapsed <= 0
                    or elapsed > MAX_SPEED_INTERVAL_S):
                continue
            coordinate_pairs += 1
            distance = haversine_m(left_coordinate, right_coordinate)
            speed = distance / elapsed * 3.6
            if distance >= MIN_SPEED_DISTANCE_M and speed > IMPOSSIBLE_SPEED_KPH:
                speeds.append({
                    "operator": operator,
                    "vehicle_ref": vehicle,
                    "route": (left["route"] if left["route"] == right["route"]
                              else f"{left['route']} -> {right['route']}"),
                    "from_trip_id": left["trip_id"],
                    "to_trip_id": right["trip_id"],
                    "from_stop_code": left["stop_code"],
                    "to_stop_code": right["stop_code"],
                    "from_recorded_at": left["recorded_at"],
                    "to_recorded_at": right["recorded_at"],
                    "elapsed_seconds": int(elapsed),
                    "distance_m": round(distance),
                    "implied_speed_kph": round(speed, 1),
                })

        ordered_intervals = sorted(intervals.values(), key=lambda item: item["start"])
        for index, left in enumerate(ordered_intervals):
            for right in ordered_intervals[index + 1:]:
                if right["start"] > left["end"]:
                    break
                overlap = (min(left["end"], right["end"])
                           - max(left["start"], right["start"])).total_seconds()
                if overlap <= 0:
                    continue
                overlaps.append({
                    "operator": operator,
                    "vehicle_ref": vehicle,
                    "left_service_date": left["service_date"],
                    "left_trip_id": left["trip_id"],
                    "left_route": left["route"],
                    "right_service_date": right["service_date"],
                    "right_trip_id": right["trip_id"],
                    "right_route": right["route"],
                    "route": (left["route"] if left["route"] == right["route"]
                              else f"{left['route']} -> {right['route']}"),
                    "overlap_seconds": int(overlap),
                    "overlap_start": max(left["start"], right["start"]).isoformat(),
                    "overlap_end": min(left["end"], right["end"]).isoformat(),
                })

    distances = [int(row["gps_distance_m"]) for row in rows
                 if row.get("gps_distance_m") is not None]
    return {
        "extreme_delays": bounded(extreme),
        "excluded_origin_timing_points": bounded(origin_timing_points),
        "backwards_stop_progress": bounded(backwards),
        "timetable_stop_transition_speeds": bounded(speeds),
        "overlapping_vehicle_trips": bounded(overlaps),
        "gps_near_match_gate": bounded(near_gate),
        "gps_distance_m": {
            "observations": len(distances),
            "p50": percentile(distances, 0.50),
            "p95": percentile(distances, 0.95),
            "p99": percentile(distances, 0.99),
            "max": max(distances) if distances else None,
        },
        "implied_speed_coordinate_pairs_checked": coordinate_pairs,
    }


def generate_report(audit_db: Path, timetable_db: Path, *,
                    now: datetime | None = None, window_hours: int = 48) -> dict:
    end = (now or utcnow()).astimezone(timezone.utc)
    start = end - timedelta(hours=window_hours)
    midpoint = start + (end - start) / 2
    comparison_start = midpoint - timedelta(days=7)
    comparison_end = end - timedelta(days=7)
    with open_read_only(audit_db) as audit, open_read_only(timetable_db) as timetable:
        require_columns(audit, "timepoint_observations", {
            "service_date", "operator", "route", "trip_id", "stop_sequence",
            "stop_code", "scheduled_local", "observed_delay_s", "gps_distance_m",
            "recorded_at", "vehicle_ref", "is_origin"})
        require_columns(audit, "poll_log", {
            "poll_at", "ok", "vehicles_total", "candidates", "matched",
            "obs_written", "dropped_insane", "stale"})
        observations = [dict(row) for row in audit.execute(
            """SELECT service_date, operator, route, trip_id, stop_sequence,
                      stop_code, scheduled_local, observed_delay_s,
                      gps_distance_m, recorded_at, vehicle_ref, is_origin
                 FROM timepoint_observations
                WHERE datetime(recorded_at) >= datetime(?)
                  AND datetime(recorded_at) <= datetime(?)
                ORDER BY recorded_at, operator, vehicle_ref""",
            (start.isoformat(), end.isoformat()))]
        comparison_polls = [dict(row) for row in audit.execute(
            """SELECT poll_at, ok, vehicles_total, candidates, matched,
                      obs_written, dropped_insane, stale
                 FROM poll_log
                WHERE datetime(poll_at) >= datetime(?)
                  AND datetime(poll_at) <= datetime(?)
                ORDER BY poll_at""",
            (comparison_start.isoformat(), end.isoformat()))]
        coordinates = load_stop_coordinates(timetable)

    polls = [row for row in comparison_polls
             if (parse_time(row["poll_at"]) or end) >= start]
    same_period_previous_week = [
        row for row in comparison_polls
        if comparison_start <= (parse_time(row["poll_at"]) or end)
        <= comparison_end
    ]

    older_polls = [row for row in polls
                   if (parse_time(row["poll_at"]) or end) < midpoint]
    recent_polls = [row for row in polls
                    if (parse_time(row["poll_at"]) or start) >= midpoint]
    older = poll_metrics(older_polls)
    recent = poll_metrics(recent_polls)
    analysis = analyse_observations(observations, coordinates)
    anomaly_count = sum(analysis[name]["count"] for name in (
        "extreme_delays", "backwards_stop_progress",
        "timetable_stop_transition_speeds", "overlapping_vehicle_trips",
        "gps_near_match_gate"))
    recent_metrics = poll_metrics(recent_polls)
    previous_week_metrics = poll_metrics(same_period_previous_week)
    return {
        "schema_version": 2,
        "generated_at": end.isoformat(),
        "mode": "read_only_report",
        "status": "attention" if anomaly_count else "clear",
        "window": {
            "hours": window_hours,
            "start": start.isoformat(),
            "midpoint": midpoint.isoformat(),
            "end": end.isoformat(),
        },
        "thresholds": {
            "extreme_late_seconds": EXTREME_LATE_S,
            "extreme_early_seconds": EXTREME_EARLY_S,
            "timetable_stop_transition_speed_kph": IMPOSSIBLE_SPEED_KPH,
            "minimum_speed_distance_m": MIN_SPEED_DISTANCE_M,
            "gps_near_match_gate_m": GPS_NEAR_GATE_M,
            "evidence_limit_per_detector": EVIDENCE_LIMIT,
        },
        "coverage": {
            "observations": len(observations),
            "polls": len(polls),
            "timetable_stop_codes": len(coordinates),
        },
        "poll_metrics": {
            "full_window": poll_metrics(polls),
            "older_half": older,
            "recent_half": recent_metrics,
            "same_period_previous_week": previous_week_metrics,
            "recent_minus_older": {
                name: metric_change(older, recent, name)
                for name in (
                    "match_rate", "rejected_readings", "dropped_insane", "stale")
            },
            "recent_minus_same_period_previous_week": {
                name: metric_change(previous_week_metrics, recent_metrics, name)
                for name in (
                    "match_rate", "rejected_readings", "dropped_insane", "stale")
            },
        },
        "detectors": analysis,
        "notes": [
            "No collector, timetable or published punctuality data was changed.",
            "Origin timing points are retained as evidence but excluded from published punctuality and extreme-delay alerts.",
            "Stop-transition speed uses matched timetable-stop coordinates; it screens audit progression and is not evidence about raw GPS movement.",
            "GPS near-gate flags readings at or above 900m; the collector rejects matches beyond 1000m.",
        ],
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, required=True)
    parser.add_argument("--timetable-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--window-hours", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.window_hours <= 24 * 14:
        raise SystemExit("--window-hours must be between 1 and 336")
    report = generate_report(
        args.audit_db, args.timetable_db, window_hours=args.window_hours)
    stamp = report["generated_at"].replace(":", "").replace("+00:00", "Z")
    atomic_json(args.history_dir / f"{stamp}.json", report)
    atomic_json(args.output, report)
    print(json.dumps({
        "status": report["status"],
        "window": report["window"],
        "coverage": report["coverage"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
