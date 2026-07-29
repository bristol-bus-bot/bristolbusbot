#!/usr/bin/env python3
"""Bounded semantic comparison of two production timetable databases."""
from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping
from zoneinfo import ZoneInfo


BRISTOL_TZ = ZoneInfo("Europe/London")
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)
MAX_DATABASE_BYTES = 512 * 1024 * 1024
SQLITE_CACHE_KIB = 8 * 1024
DEFAULT_DEADLINE_SECONDS = 8 * 60
MAX_RECORDED_FAILURES = 100


@dataclass(frozen=True)
class AcceptancePolicy:
    """All acceptance thresholds live in one recorded policy."""

    version: str = "service-window-v1"
    near_term_days: int = 28
    minimum_forward_days: int = 180
    maximum_forward_days: int = 400
    daily_ratio: float = 0.85
    near_term_total_ratio: float = 0.90
    route_coverage_ratio: float = 0.80
    substantial_operator_ratio: float = 0.70
    substantial_operator_share: float = 0.01
    forward_coverage_ratio: float = 0.75
    raw_catastrophic_ratio: float = 0.25

    def record(self) -> dict[str, object]:
        return {
            "version": self.version,
            "near_term_days": self.near_term_days,
            "minimum_forward_days": self.minimum_forward_days,
            "maximum_forward_days": self.maximum_forward_days,
            "daily_ratio": self.daily_ratio,
            "near_term_total_ratio": self.near_term_total_ratio,
            "route_coverage_ratio": self.route_coverage_ratio,
            "substantial_operator_ratio": self.substantial_operator_ratio,
            "substantial_operator_share": self.substantial_operator_share,
            "forward_coverage_ratio": self.forward_coverage_ratio,
            "raw_catastrophic_ratio": self.raw_catastrophic_ratio,
        }


DEFAULT_POLICY = AcceptancePolicy()


class ServiceProfileError(RuntimeError):
    """A bounded, safely reportable profile or comparison failure."""

    def __init__(self, code: str, message: str,
                 context: Mapping[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.context = dict(context or {})


@dataclass(frozen=True)
class TripVolume:
    operator: str
    route: str
    direction: int
    trips: int
    stop_times: int


@dataclass
class ServiceProfile:
    start_date: date
    horizon_days: int
    daily: list[dict[str, object]]
    operator_totals: dict[str, dict[str, int]]
    route_totals: dict[tuple[str, str], dict[str, int]]
    route_direction_totals: dict[tuple[str, str, int], dict[str, int]]
    route_shapes: dict[tuple[str, str, int], set[str]]
    query_seconds: float
    peak_query_seconds: float

    def summary(self) -> dict[str, object]:
        last_service = next((
            str(day["date"]) for day in reversed(self.daily)
            if int(day["trips"]) > 0
        ), None)
        return {
            "start_date": self.start_date.isoformat(),
            "horizon_days": self.horizon_days,
            "last_service_date": last_service,
            "query_seconds": round(self.query_seconds, 3),
            "peak_query_seconds": round(self.peak_query_seconds, 3),
        }


VOLUME_SQL = """
WITH trip_stop_counts AS (
    SELECT trip_id, COUNT(*) AS stop_times
    FROM stop_times
    GROUP BY trip_id
)
SELECT t.service_id,
       COALESCE(NULLIF(a.agency_noc, ''), a.agency_id, 'unknown') AS operator,
       COALESCE(NULLIF(r.route_short_name, ''), r.route_id, 'unknown') AS route,
       COALESCE(t.direction_id, 0) AS direction_id,
       COUNT(*) AS trips,
       SUM(COALESCE(ts.stop_times, 0)) AS stop_times
FROM trips AS t
JOIN routes AS r ON r.route_id = t.route_id
JOIN agency AS a ON a.agency_id = r.agency_id
LEFT JOIN trip_stop_counts AS ts ON ts.trip_id = t.trip_id
GROUP BY t.service_id, operator, route, direction_id
ORDER BY t.service_id, operator, route, direction_id
"""

CALENDAR_SQL = """
SELECT service_id, monday, tuesday, wednesday, thursday,
       friday, saturday, sunday, start_date, end_date
FROM calendar
ORDER BY service_id
"""

CALENDAR_DATES_SQL = """
SELECT service_id, date, exception_type
FROM calendar_dates
ORDER BY date, service_id
"""

SHAPES_SQL = """
SELECT operator_noc, route_name, COALESCE(direction_id, 0), points_json
FROM route_shapes
ORDER BY operator_noc, route_name, direction_id, variant
"""


def bristol_today(now: datetime | None = None) -> date:
    current = now or datetime.now(BRISTOL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BRISTOL_TZ)
    return current.astimezone(BRISTOL_TZ).date()


def _parse_service_date(value: object, field: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError as exc:
        raise ServiceProfileError(
            "malformed_calendar", f"invalid {field} service date") from exc


def _open_database(path: Path, deadline: float,
                   monotonic: Callable[[], float]) -> sqlite3.Connection:
    try:
        details = path.stat()
    except OSError as exc:
        raise ServiceProfileError(
            "database_unavailable", "timetable database is unavailable") from exc
    if not path.is_file() or details.st_size <= 0:
        raise ServiceProfileError(
            "database_unavailable", "timetable database is not a regular file")
    if details.st_size > MAX_DATABASE_BYTES:
        raise ServiceProfileError(
            "database_too_large", "timetable database exceeds the comparison limit",
            {"bytes": details.st_size, "maximum": MAX_DATABASE_BYTES})
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.set_progress_handler(
            lambda: 1 if monotonic() >= deadline else 0, 10_000)
        return connection
    except sqlite3.Error as exc:
        raise ServiceProfileError(
            "database_profile_failed", "timetable database could not be opened") from exc


def _timed_rows(connection: sqlite3.Connection, sql: str, deadline: float,
                monotonic: Callable[[], float]) -> tuple[list[sqlite3.Row], float]:
    started = monotonic()
    try:
        rows = list(connection.execute(sql))
    except sqlite3.OperationalError as exc:
        if monotonic() >= deadline or "interrupted" in str(exc).lower():
            raise ServiceProfileError(
                "database_profile_timeout", "timetable comparison exceeded its deadline") from exc
        raise ServiceProfileError(
            "database_profile_failed", "timetable database could not be profiled") from exc
    except sqlite3.Error as exc:
        raise ServiceProfileError(
            "database_profile_failed", "timetable database could not be profiled") from exc
    return rows, monotonic() - started


def _active_services(day: date, calendars: Iterable[sqlite3.Row],
                     exceptions: Mapping[date, Mapping[str, int]]) -> set[str]:
    weekday = WEEKDAYS[day.weekday()]
    active = {
        str(row["service_id"])
        for row in calendars
        if _parse_service_date(row["start_date"], "calendar start") <= day
        <= _parse_service_date(row["end_date"], "calendar end")
        and int(row[weekday]) == 1
    }
    for service_id, exception_type in exceptions.get(day, {}).items():
        if exception_type == 1:
            active.add(service_id)
        elif exception_type == 2:
            active.discard(service_id)
        else:
            raise ServiceProfileError(
                "malformed_calendar", "calendar exception type is invalid")
    return active


def build_service_profile(path: Path, *, start_date: date,
                          policy: AcceptancePolicy = DEFAULT_POLICY,
                          deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                          monotonic: Callable[[], float] = time.monotonic) -> ServiceProfile:
    """Read fixed aggregates once, then derive dated service entirely in Python."""
    overall_started = monotonic()
    deadline = overall_started + deadline_seconds
    connection = _open_database(path, deadline, monotonic)
    durations: list[float] = []
    try:
        volume_rows, elapsed = _timed_rows(
            connection, VOLUME_SQL, deadline, monotonic)
        durations.append(elapsed)
        calendar_rows, elapsed = _timed_rows(
            connection, CALENDAR_SQL, deadline, monotonic)
        durations.append(elapsed)
        exception_rows, elapsed = _timed_rows(
            connection, CALENDAR_DATES_SQL, deadline, monotonic)
        durations.append(elapsed)
        shape_rows, elapsed = _timed_rows(
            connection, SHAPES_SQL, deadline, monotonic)
        durations.append(elapsed)
    finally:
        connection.close()

    volumes: dict[str, list[TripVolume]] = defaultdict(list)
    for row in volume_rows:
        service_id = str(row["service_id"] or "")
        if not service_id:
            raise ServiceProfileError(
                "malformed_calendar", "trip has no service identity")
        volumes[service_id].append(TripVolume(
            operator=str(row["operator"]),
            route=str(row["route"]),
            direction=int(row["direction_id"]),
            trips=int(row["trips"]),
            stop_times=int(row["stop_times"]),
        ))

    exceptions: dict[date, dict[str, int]] = defaultdict(dict)
    latest_date = start_date + timedelta(days=policy.minimum_forward_days - 1)
    for row in calendar_rows:
        service_id = str(row["service_id"] or "")
        start = _parse_service_date(row["start_date"], "calendar start")
        end = _parse_service_date(row["end_date"], "calendar end")
        try:
            weekday_values = [int(row[field]) for field in WEEKDAYS]
        except (TypeError, ValueError) as exc:
            raise ServiceProfileError(
                "malformed_calendar", "calendar weekday flag is invalid") from exc
        if not service_id or start > end \
                or any(value not in {0, 1} for value in weekday_values):
            raise ServiceProfileError(
                "malformed_calendar", "calendar service definition is invalid")
        latest_date = max(
            latest_date,
            end)
    for row in exception_rows:
        service_day = _parse_service_date(row["date"], "calendar exception")
        service_id = str(row["service_id"] or "")
        try:
            exception_type = int(row["exception_type"])
        except (TypeError, ValueError) as exc:
            raise ServiceProfileError(
                "malformed_calendar", "calendar exception type is invalid") from exc
        if not service_id or service_id in exceptions[service_day] \
                or exception_type not in {1, 2}:
            raise ServiceProfileError(
                "malformed_calendar", "calendar exception identity is invalid")
        exceptions[service_day][service_id] = exception_type
        latest_date = max(latest_date, service_day)

    requested_horizon = max(
        policy.minimum_forward_days,
        (latest_date - start_date).days + 1,
    )
    horizon_days = min(policy.maximum_forward_days, requested_horizon)
    route_shapes: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for operator, route, direction, points in shape_rows:
        if points is None:
            continue
        digest = hashlib.sha256(str(points).encode("utf-8")).hexdigest()
        route_shapes[(str(operator), str(route), int(direction))].add(digest)

    operator_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"trips": 0, "stop_times": 0})
    route_totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"trips": 0, "stop_times": 0})
    route_direction_totals: dict[tuple[str, str, int], dict[str, int]] = defaultdict(
        lambda: {"trips": 0, "stop_times": 0})
    daily: list[dict[str, object]] = []
    for offset in range(horizon_days):
        if monotonic() >= deadline:
            raise ServiceProfileError(
                "database_profile_timeout", "timetable comparison exceeded its deadline")
        service_day = start_date + timedelta(days=offset)
        active = _active_services(service_day, calendar_rows, exceptions)
        trips = stop_times = 0
        routes: set[tuple[str, str]] = set()
        day_operators: dict[str, dict[str, int]] = defaultdict(
            lambda: {"trips": 0, "stop_times": 0})
        for service_id in active:
            for volume in volumes.get(service_id, ()):
                trips += volume.trips
                stop_times += volume.stop_times
                routes.add((volume.operator, volume.route))
                for target in (day_operators[volume.operator],):
                    target["trips"] += volume.trips
                    target["stop_times"] += volume.stop_times
                if offset < policy.near_term_days:
                    for target in (
                        operator_totals[volume.operator],
                        route_totals[(volume.operator, volume.route)],
                        route_direction_totals[(
                            volume.operator, volume.route, volume.direction)],
                    ):
                        target["trips"] += volume.trips
                        target["stop_times"] += volume.stop_times
        daily.append({
            "date": service_day.isoformat(),
            "trips": trips,
            "stop_times": stop_times,
            "routes": routes,
            "operators": dict(day_operators),
            "active_service_ids": len(active),
        })
    return ServiceProfile(
        start_date=start_date,
        horizon_days=horizon_days,
        daily=daily,
        operator_totals=dict(operator_totals),
        route_totals=dict(route_totals),
        route_direction_totals=dict(route_direction_totals),
        route_shapes=dict(route_shapes),
        query_seconds=monotonic() - overall_started,
        peak_query_seconds=max(durations, default=0.0),
    )


def _minimum(current: int, ratio: float) -> int:
    return math.ceil(current * ratio)


def _gate(metric: str, current: int, candidate: int, ratio: float,
          **identity: object) -> dict[str, object]:
    minimum = _minimum(current, ratio) if current > 0 else 0
    return {
        **identity,
        "metric": metric,
        "current": current,
        "candidate": candidate,
        "minimum": minimum,
        "ratio": round(candidate / current, 6) if current else None,
        "passed": candidate >= minimum,
    }


def _compatible_transfers(current: ServiceProfile, candidate: ServiceProfile,
                          operator: str,
                          used: set[tuple[str, str, int]]) \
        -> tuple[dict[str, int], list[dict[str, object]]]:
    transferred = {"trips": 0, "stop_times": 0}
    warnings: list[dict[str, object]] = []
    for live_key, live_volume in current.route_direction_totals.items():
        live_operator, route, direction = live_key
        if live_operator != operator:
            continue
        live_shapes = current.route_shapes.get(live_key, set())
        if not live_shapes:
            continue
        matches = []
        for candidate_key, candidate_volume in candidate.route_direction_totals.items():
            candidate_operator, candidate_route, candidate_direction = candidate_key
            if candidate_operator == operator or candidate_key in used:
                continue
            if candidate_route != route or candidate_direction != direction:
                continue
            if live_shapes.isdisjoint(candidate.route_shapes.get(candidate_key, set())):
                continue
            matches.append((candidate_key, candidate_volume))
        if not matches:
            continue
        candidate_key, candidate_volume = max(
            matches, key=lambda item: int(item[1]["stop_times"]))
        used.add(candidate_key)
        for metric in ("trips", "stop_times"):
            transferred[metric] += min(
                int(live_volume[metric]), int(candidate_volume[metric]))
        warnings.append({
            "code": "operator_transfer",
            "from_operator": operator,
            "to_operator": candidate_key[0],
            "route": route,
            "direction": direction,
            "trips": min(int(live_volume["trips"]),
                         int(candidate_volume["trips"])),
            "stop_times": min(int(live_volume["stop_times"]),
                              int(candidate_volume["stop_times"])),
        })
    return transferred, warnings


def compare_service_profiles(current: ServiceProfile, candidate: ServiceProfile,
                             *, policy: AcceptancePolicy = DEFAULT_POLICY) -> dict[str, object]:
    if current.start_date != candidate.start_date:
        raise ServiceProfileError(
            "profile_identity_mismatch", "service profiles use different start dates")
    if len(current.daily) < policy.minimum_forward_days \
            or len(candidate.daily) < policy.minimum_forward_days:
        raise ServiceProfileError(
            "future_coverage_short", "service profile does not cover the required horizon")

    near_term: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for offset in range(policy.near_term_days):
        live_day = current.daily[offset]
        new_day = candidate.daily[offset]
        for metric in ("trips", "stop_times"):
            gate = _gate(
                metric, int(live_day[metric]), int(new_day[metric]),
                policy.daily_ratio, kind="near_term_day",
                date=live_day["date"])
            near_term.append(gate)
            if not gate["passed"] and len(failures) < MAX_RECORDED_FAILURES:
                failures.append(gate)

    totals: list[dict[str, object]] = []
    for metric in ("trips", "stop_times"):
        live_total = sum(int(day[metric]) for day in current.daily[:policy.near_term_days])
        candidate_total = sum(
            int(day[metric]) for day in candidate.daily[:policy.near_term_days])
        gate = _gate(
            metric, live_total, candidate_total,
            policy.near_term_total_ratio, kind="near_term_total",
            days=policy.near_term_days)
        totals.append(gate)
        if not gate["passed"] and len(failures) < MAX_RECORDED_FAILURES:
            failures.append(gate)

    live_routes = {
        route for day in current.daily[:policy.near_term_days]
        for route in day["routes"]
    }
    candidate_routes = {
        route for day in candidate.daily[:policy.near_term_days]
        for route in day["routes"]
    }
    covered_routes = live_routes.intersection(candidate_routes)
    for live_operator, route in live_routes - covered_routes:
        live_directions = {
            direction: shapes
            for (operator, route_name, direction), shapes
            in current.route_shapes.items()
            if operator == live_operator and route_name == route
        }
        if live_directions and all(any(
                candidate_route == route
                and candidate_direction == direction
                and candidate_operator != live_operator
                and not shapes.isdisjoint(candidate_shapes)
                for (candidate_operator, candidate_route, candidate_direction),
                candidate_shapes in candidate.route_shapes.items()
            ) for direction, shapes in live_directions.items()):
            covered_routes.add((live_operator, route))
    route_gate = _gate(
        "operator_routes", len(live_routes), len(covered_routes),
        policy.route_coverage_ratio, kind="route_coverage",
        candidate_total=len(candidate_routes),
        missing=len(live_routes - covered_routes))

    live_stop_times = sum(
        int(day["stop_times"]) for day in current.daily[:policy.near_term_days])
    substantial_minimum = _minimum(
        live_stop_times, policy.substantial_operator_share)
    operators: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    used_transfers: set[tuple[str, str, int]] = set()
    for operator, live_volume in sorted(current.operator_totals.items()):
        if int(live_volume["stop_times"]) < substantial_minimum:
            continue
        candidate_volume = candidate.operator_totals.get(
            operator, {"trips": 0, "stop_times": 0})
        transferred, transfer_warnings = _compatible_transfers(
            current, candidate, operator, used_transfers)
        adjusted = {
            metric: int(candidate_volume[metric]) + int(transferred[metric])
            for metric in ("trips", "stop_times")
        }
        operator_result = {
            "operator": operator,
            "substantial_minimum_stop_times": substantial_minimum,
            "transferred": transferred,
            "gates": [],
        }
        for metric in ("trips", "stop_times"):
            gate = _gate(
                metric, int(live_volume[metric]), adjusted[metric],
                policy.substantial_operator_ratio, kind="operator",
                operator=operator,
                direct_candidate=int(candidate_volume[metric]),
                compatible_transfer=int(transferred[metric]))
            operator_result["gates"].append(gate)
            if not gate["passed"] and len(failures) < MAX_RECORDED_FAILURES:
                failures.append(gate)
        if transfer_warnings and all(
                bool(gate["passed"]) for gate in operator_result["gates"]):
            warnings.extend(transfer_warnings)
        operators.append(operator_result)

    if not route_gate["passed"] and len(failures) < MAX_RECORDED_FAILURES:
        failures.append(route_gate)

    forward: list[dict[str, object]] = []
    forward_days = min(len(current.daily), len(candidate.daily))
    for offset in range(policy.near_term_days, forward_days):
        live_day = current.daily[offset]
        new_day = candidate.daily[offset]
        for metric in ("trips", "stop_times"):
            if int(live_day[metric]) <= 0:
                continue
            gate = _gate(
                metric, int(live_day[metric]), int(new_day[metric]),
                policy.forward_coverage_ratio, kind="forward_coverage",
                date=live_day["date"])
            if not gate["passed"] and len(failures) < MAX_RECORDED_FAILURES:
                failures.append(gate)
            forward.append(gate)

    if failures:
        first = failures[0]
        kind = str(first.get("kind"))
        if kind == "operator":
            code = "candidate_operator_collapse"
        elif kind == "forward_coverage":
            code = "candidate_future_coverage_cliff"
        else:
            code = "candidate_service_collapse"
        context = {
            key: first[key]
            for key in (
                "kind", "metric", "date", "operator", "current",
                "candidate", "minimum", "ratio",
            ) if key in first
        }
        context["policy_version"] = policy.version
        raise ServiceProfileError(
            code, "candidate usable service is below the safe minimum", context)

    worst_forward = sorted(
        forward,
        key=lambda gate: float(gate["ratio"])
        if gate["ratio"] is not None else 1.0,
    )[:10]
    return {
        "policy": policy.record(),
        "current_profile": current.summary(),
        "candidate_profile": candidate.summary(),
        "near_term_daily": near_term,
        "near_term_totals": totals,
        "route_coverage": route_gate,
        "operators": operators,
        "forward_worst": worst_forward,
        "warnings": warnings,
        "status": "pass",
    }


def compare_databases(current_path: Path, candidate_path: Path, *,
                      start_date: date | None = None,
                      policy: AcceptancePolicy = DEFAULT_POLICY,
                      deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                      monotonic: Callable[[], float] = time.monotonic) -> dict[str, object]:
    """Build both profiles under one deadline and apply the recorded policy."""
    effective_start = start_date or bristol_today()
    started = monotonic()
    current = build_service_profile(
        current_path,
        start_date=effective_start,
        policy=policy,
        deadline_seconds=deadline_seconds,
        monotonic=monotonic,
    )
    remaining = deadline_seconds - (monotonic() - started)
    if remaining <= 0:
        raise ServiceProfileError(
            "database_profile_timeout", "timetable comparison exceeded its deadline")
    candidate = build_service_profile(
        candidate_path,
        start_date=effective_start,
        policy=policy,
        deadline_seconds=remaining,
        monotonic=monotonic,
    )
    result = compare_service_profiles(current, candidate, policy=policy)
    result["duration_seconds"] = round(monotonic() - started, 3)
    return result
