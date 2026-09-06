"""Read-only map presentation; never writes collector or audit measurements.

Route shapes are representative, not trip-specific. Every trip stop must fit
in order, with unique enclosing stops and vehicle position. Ambiguity fails closed.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from collector.geo import haversine_m
from collector.timeparse import gtfs_seconds, service_midnight, scheduled_local

LDN = ZoneInfo("Europe/London")


def _projections(point, shape):
    if not 2 <= len(shape) <= 5000:
        return []
    scale_x = 111195 * math.cos(math.radians(point[0]))
    candidates = []
    offset = 0.0
    for a, b in zip(shape, shape[1:]):
        ax, ay = (a[1] - point[1]) * scale_x, (a[0] - point[0]) * 111195
        bx, by = (b[1] - point[1]) * scale_x, (b[0] - point[0]) * 111195
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        fraction = max(0, min(1, -(ax * dx + ay * dy) / length**2))
        distance = math.hypot(ax + fraction * dx, ay + fraction * dy)
        candidates.append((offset + fraction * length, distance))
        offset += length
    if not candidates:
        return []
    best = min(candidates, key=lambda x: x[1])
    if best[1] > 100:
        return []
    nearby = sorted(p for p in candidates if p[1] <= min(100, best[1] + 25))
    groups = []
    for candidate in nearby:
        if groups and candidate[0] - groups[-1][0][0] <= 100:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return [min(group, key=lambda p: p[1]) for group in groups]


def project(point, shape):
    """Return distance along path and distance away, or None at ambiguous loops."""
    candidates = _projections(point, shape)
    return candidates[0] if len(candidates) == 1 else None


def ordered_stops(schedule, shape):
    """Keep only stop locations consistent with a complete ordered path."""
    candidates = [_projections((s[3], s[4]), shape) for s in schedule]
    if any(not options or len(options) > 16 for options in candidates):
        return None
    # Prune locations with no complete path from the first or to the last stop.
    for i in range(1, len(candidates)):
        candidates[i] = [b for b in candidates[i]
                         if any(b[0] - a[0] >= 10 for a in candidates[i - 1])]
    for i in range(len(candidates) - 2, -1, -1):
        candidates[i] = [a for a in candidates[i]
                         if any(b[0] - a[0] >= 10 for b in candidates[i + 1])]
    if any(not options for options in candidates):
        return None
    return candidates


def display_timing(row, schedule, shapes, now):
    """Schedule rows: sequence, arrival, departure, latitude, longitude, name."""
    if not row["trip_id"] or len(schedule) < 2:
        return {}
    try:
        recorded = datetime.fromisoformat(row["recorded_at"])
        if recorded.tzinfo is None or not 0 <= (now - recorded).total_seconds() <= 90:
            return {}
        origin = row["origin_aimed_departure"]
        if origin:
            origin = datetime.fromisoformat(origin).astimezone(LDN)
        else:
            # Same HHMM fallback as the collector, restricted to daytime here.
            ref = row["journey_ref"] or ""
            if len(ref) != 4 or not ref.isdigit() or not 6 <= int(ref[:2]) < 24:
                return {}
            origin = recorded.astimezone(LDN).replace(
                hour=int(ref[:2]), minute=int(ref[2:]), second=0, microsecond=0)
        first = gtfs_seconds(schedule[0][2])
        if first is None:
            return {}
        midnight = service_midnight(origin, first)
        arrivals = [gtfs_seconds(s[1]) for s in schedule]
        departures = [gtfs_seconds(s[2]) for s in schedule]
        if any(t is None for t in arrivals + departures):
            return {}
        if any(a > d for a, d in zip(arrivals, departures)):
            return {}
        if any(d > a for d, a in zip(departures, arrivals[1:])):
            return {}
        elapsed = (recorded - midnight.astimezone(timezone.utc)).total_seconds()
        current = (now - midnight.astimezone(timezone.utc)).total_seconds()
        distances = [haversine_m(row["lat"], row["lon"], s[3], s[4]) for s in schedule]
        waiting = []
        for i, distance in enumerate(distances):
            if distance > 75 or i == len(schedule) - 1:
                continue
            start = departures[i] - 3600 if i == 0 else arrivals[i] - 300
            if i != 0 and departures[i] - arrivals[i] < 120:
                continue
            if start <= elapsed < departures[i] and current < departures[i]:
                waiting.append(i)
        if len(waiting) == 1:
            i = waiting[0]
            return {"eventType": "waiting", "delayMinutes": None,
                    "waitingAtOrigin": i == 0, "waitingAtStop": i != 0,
                    "timingSource": "scheduled_wait",
                    "scheduledDeparture": scheduled_local(midnight, departures[i]).isoformat()}
        if row["delay_seconds"] is not None or min(distances) <= 1000:
            return {}
        estimates = []
        for shape in shapes:
            stops = ordered_stops(schedule, shape)
            if stops is None:
                continue
            position = project((row["lat"], row["lon"]), shape)
            if position is None:
                return {}
            estimate_count = len(estimates)
            for i, (left, right) in enumerate(zip(stops, stops[1:])):
                # Ambiguity elsewhere (e.g. a terminal loop) does not affect
                # this estimate, but both enclosing stops must be unique.
                if len(left) != 1 or len(right) != 1:
                    continue
                a, b = left[0], right[0]
                if not a[0] < position[0] < b[0] or b[0] - a[0] > 30000:
                    continue
                duration = arrivals[i + 1] - departures[i]
                if not 0 < duration <= 3600:
                    continue
                fraction = (position[0] - a[0]) / (b[0] - a[0])
                estimates.append(elapsed - (departures[i] + fraction * duration))
            if len(estimates) == estimate_count:
                return {}
        if not estimates or max(estimates) - min(estimates) > 60:
            return {}
        delay = sum(estimates) / len(estimates)
        if not -900 <= delay <= 5400:
            return {}
        minutes = round(delay / 60)
        return {"delayMinutes": minutes, "timingSource": "route_estimate",
                "eventType": "delayed" if minutes >= 4 else "early" if minutes <= -3 else "punctual"}
    except (ValueError, TypeError, IndexError, OverflowError):
        return {}


class MapTiming:
    """Request-scoped lookup caches; timetable replacement cannot leave stale data."""

    def __init__(self, conn):
        self.conn = conn
        self.schedules = {}
        self.shapes = {}
        self.has_shapes = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='route_shapes'").fetchone()

    def for_vehicle(self, row, now):
        if not row["trip_id"] or (row["delay_seconds"] is not None and row["delay_seconds"] >= 0):
            return {}
        trip = row["trip_id"]
        if trip not in self.schedules:
            self.schedules[trip] = self.conn.execute(
                "SELECT st.stop_sequence,st.arrival_time,st.departure_time,"
                "s.stop_lat,s.stop_lon,s.stop_name FROM stop_times st "
                "JOIN stops s ON s.stop_id=st.stop_id WHERE st.trip_id=? "
                "ORDER BY st.stop_sequence", (trip,)).fetchall()
        shapes = []
        # Only the collector's matched trip determines route and direction.
        if row["delay_seconds"] is None and self.has_shapes:
            key = self.conn.execute(
                "SELECT r.route_short_name,a.agency_noc,t.direction_id FROM trips t "
                "JOIN routes r ON r.route_id=t.route_id JOIN agency a ON a.agency_id=r.agency_id "
                "WHERE t.trip_id=?", (trip,)).fetchone()
            if key:
                key = tuple(key)
                if key not in self.shapes:
                    values = self.conn.execute(
                        "SELECT points_json FROM route_shapes WHERE route_name=? "
                        "AND operator_noc=? AND direction_id=? LIMIT 9", key).fetchall()
                    try:
                        self.shapes[key] = [json.loads(v[0]) for v in values] if len(values) < 9 else []
                    except (ValueError, TypeError):
                        self.shapes[key] = []
                shapes = self.shapes[key]
        return display_timing(row, self.schedules[trip], shapes, now)
