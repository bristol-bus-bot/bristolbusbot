"""How late each route's trips start, morning against later in the day.

A route that loses time on every trip, with no slack at the end of the route
to recover, starts each trip later than the last: on time first thing, well
behind by the afternoon. For each route over the audit window this measures
the lateness at the first timing point after the origin (the origin itself is
excluded because buses wait there), for trips starting 07:00-10:00 and
13:00-19:00, and how often the same bus started its later trips at least two
minutes later than its earlier ones that day.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

import route_split

MORNING = range(7, 10)
LATER = range(13, 19)
MIN_TRIPS = 20
MIN_BUS_DAYS = 10
RISE_S = 120


def _hour(scheduled_local) -> int | None:
    try:
        return int(str(scheduled_local)[11:13])
    except (TypeError, ValueError):
        return None


def build(conn, window, public_route, network_label: str) -> dict[tuple[str, str], dict]:
    """{(operator or network label, route label): figures} for routes with enough data."""
    if not window:
        return {}
    split = route_split.for_connection(conn)
    placeholders = ",".join("?" for _ in window)
    first: dict[tuple, tuple] = {}
    try:
        rows = conn.execute(
            f"""SELECT service_date, operator, route, trip_id, stop_code, vehicle_ref,
                       observed_delay_s, scheduled_local
                  FROM timepoint_observations
                 WHERE service_date IN ({placeholders}) AND COALESCE(is_origin, 0) = 0
                   AND gps_distance_m IS NOT NULL AND gps_distance_m <= 150
                   AND observed_delay_s IS NOT NULL AND route IS NOT NULL
                 ORDER BY service_date, trip_id, stop_sequence""", list(window))
        for day, operator, route, trip, stop, vehicle, delay, scheduled in rows:
            key = (day, trip)
            if key not in first:
                label = split.route(operator, route, trip, stop)
                first[key] = (operator, label, vehicle, int(delay), _hour(scheduled))
    except Exception:  # noqa: BLE001 - an optional extra; never blocks the export
        return {}

    trips = defaultdict(lambda: {"morning": [], "later": []})
    bus_days = defaultdict(list)
    for (day, _), (operator, label, vehicle, delay, hour) in first.items():
        if hour is None:
            continue
        for owner in ((operator, label), (network_label, public_route(operator, label, True))):
            if hour in MORNING:
                trips[owner]["morning"].append(delay)
            elif hour in LATER:
                trips[owner]["later"].append(delay)
        if vehicle:
            bus_days[(operator, label, day, vehicle)].append((hour, delay))

    later_days = defaultdict(lambda: [0, 0])
    for (operator, label, _, _), runs in bus_days.items():
        if len(runs) < 4:
            continue
        runs.sort()
        half = len(runs) // 2
        rose = statistics.median(d for _, d in runs[half:]) - statistics.median(d for _, d in runs[:half]) >= RISE_S
        for owner in ((operator, label), (network_label, public_route(operator, label, True))):
            later_days[owner][0] += int(rose)
            later_days[owner][1] += 1

    out = {}
    for owner, delays in trips.items():
        rose, days = later_days.get(owner, (0, 0))
        if len(delays["morning"]) < MIN_TRIPS or len(delays["later"]) < MIN_TRIPS or days < MIN_BUS_DAYS:
            continue
        out[owner] = {
            "morning_min": round(statistics.median(delays["morning"]) / 60, 1),
            "later_min": round(statistics.median(delays["later"]) / 60, 1),
            "later_days": rose,
            "bus_days": days,
        }
    return out


def attach(summary: dict, figures: dict[tuple[str, str], dict]) -> int:
    """Add "start_lateness" to each 28-day route row that has figures."""
    added = 0
    for operator, data in ((summary.get("period") or {}).get("by_operator") or {}).items():
        for row in data.get("routes") or []:
            found = figures.get((operator, row.get("route")))
            if found:
                row["start_lateness"] = found
                added += 1
    return added
