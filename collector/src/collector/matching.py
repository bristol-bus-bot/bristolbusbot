"""Match SIRI vehicle activity to a scheduled GTFS journey.

Candidates are scoped by operator, route, direction, service day and origin
time. Position data rejects candidates whose stop sequence does not pass near
the vehicle. The optional exact journey-code tier is disabled by default
because some operators publish journey references that are not GTFS codes.

Early-morning searches also consider after-midnight GTFS times from the
previous service day. Calendar-date additions and removals are applied before
matching. Ambiguous or geographically implausible candidates are left
unmatched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .geo import haversine_m

# A matched trip must pass near the reported vehicle position.
MAX_MATCH_DISTANCE_M = 3000.0

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Schedule rows returned to callers, everywhere in the collector:
# (stop_sequence, departure_time, timepoint, stop_code, stop_lat, stop_lon,
#  stop_name)  — name appended last so index-based consumers stay valid
_SCHEDULE_SQL = """
    SELECT st.stop_sequence, st.departure_time, st.timepoint,
           s.stop_code, s.stop_lat, s.stop_lon, s.stop_name
    FROM stop_times st
    JOIN stops s ON st.stop_id = s.stop_id
    WHERE st.trip_id = ?
    ORDER BY st.stop_sequence ASC
"""


@dataclass
class Match:
    trip_id: str
    route_short_name: str
    schedule: list  # rows as above
    tier: str       # 'exact' | 'fuzzy'


def _direction_id(direction_ref: str | None) -> int | None:
    dr = (direction_ref or "").lower().strip()
    if dr == "outbound":
        return 0
    if dr == "inbound":
        return 1
    return None


def _schedule_for(cur, trip_id: str) -> list:
    cur.execute(_SCHEDULE_SQL, (trip_id,))
    return cur.fetchall()


def match_exact(cur, operator_noc: str, journey_ref: str,
                origin_local: datetime | None = None,
                line_name: str = "") -> Match | None:
    """Tier 0: trips.vehicle_journey_code. Only meaningful for operators whose
    SIRI DatedVehicleJourneyRef is a real journey code (not the HHMM start).
    An HHMM-shaped ref ('1115') is refused outright: it would collide with
    unrelated codes."""
    ref = (journey_ref or "").strip()
    if not ref or not operator_noc or (len(ref) == 4 and ref.isdigit()):
        return None
    service_days = [origin_local] if origin_local else [None]
    if origin_local and origin_local.hour < 6:
        service_days.insert(0, origin_local - timedelta(days=1))
    row = None
    for service_day in service_days:
        active_sql = ""
        line_sql = "AND r.route_short_name = ?" if line_name else ""
        params: list[object] = [ref, operator_noc]
        if line_name:
            params.append(line_name)
        if service_day:
            date_str = service_day.strftime("%Y%m%d")
            day_col = DAYS[service_day.weekday()]
            active_sql = f"""
               AND (
                   EXISTS (
                       SELECT 1 FROM calendar c
                       WHERE c.service_id=t.service_id
                         AND c.{day_col}=1
                         AND c.start_date<=? AND c.end_date>=?
                         AND NOT EXISTS (
                             SELECT 1 FROM calendar_dates removed
                             WHERE removed.service_id=t.service_id
                               AND removed.date=? AND removed.exception_type=2
                         )
                   )
                   OR EXISTS (
                       SELECT 1 FROM calendar_dates added
                       WHERE added.service_id=t.service_id
                         AND added.date=? AND added.exception_type=1
                   )
               )
            """
            params.extend([date_str, date_str, date_str, date_str])
        cur.execute(
            f"""SELECT t.trip_id, r.route_short_name
                FROM trips t
                JOIN routes r ON t.route_id = r.route_id
                JOIN agency a ON r.agency_id = a.agency_id
                WHERE t.vehicle_journey_code = ? AND a.agency_noc = ?
                {line_sql}
                {active_sql}
                LIMIT 1""",
            params,
        )
        row = cur.fetchone()
        if row:
            break
    if not row:
        return None
    schedule = _schedule_for(cur, row[0])
    if not schedule:
        return None
    return Match(trip_id=row[0], route_short_name=row[1], schedule=schedule, tier="exact")


def _gtfs_secs(t: str) -> int | None:
    try:
        h, m, sec = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def _route_near(schedule: list, lat: float, lon: float) -> bool:
    """Does this trip pass within MAX_MATCH_DISTANCE_M of the vehicle?"""
    return any(
        row[4] is not None and row[5] is not None
        and haversine_m(lat, lon, row[4], row[5]) <= MAX_MATCH_DISTANCE_M
        for row in schedule
    )


def match_fuzzy(cur, operator_noc: str, line_name: str, direction_ref: str | None,
                origin_local: datetime,
                vehicle_pos: tuple[float, float] | None = None) -> Match | None:
    """Tier 1: fuzzy matching, with deterministic
    candidate selection (nearest departure-time gap) and, when vehicle_pos
    is given, a route-proximity gate (see module docstring)."""
    if not line_name or line_name in ("Unknown", "") or not operator_noc:
        return None

    direction_id = _direction_id(direction_ref)

    today_str = origin_local.strftime("%Y%m%d")
    lo = origin_local - timedelta(minutes=10)
    hi = origin_local + timedelta(minutes=10)
    lo_t = f"{lo.hour:02d}:{lo.minute:02d}:{lo.second:02d}"
    hi_t = f"{hi.hour:02d}:{hi.minute:02d}:{hi.second:02d}"
    origin_secs = origin_local.hour * 3600 + origin_local.minute * 60 \
        + origin_local.second
    search_sets = [(lo_t, hi_t, DAYS[origin_local.weekday()], today_str,
                    origin_secs)]

    if origin_local.hour < 6:
        # A pre-dawn bus may belong to YESTERDAY's service day, timetabled
        # with hours >= 24 ("25:30"). Search that window too.
        prev = origin_local - timedelta(days=1)
        search_sets.append((
            f"{lo.hour + 24:02d}:{lo.minute:02d}:{lo.second:02d}",
            f"{hi.hour + 24:02d}:{hi.minute:02d}:{hi.second:02d}",
            DAYS[prev.weekday()], prev.strftime("%Y%m%d"),
            origin_secs + 24 * 3600,
        ))

    for use_direction in (True, False):
        for lower, upper, day_col, date_str, target_secs in search_sets:
            eff_dir = direction_id if use_direction else None
            dir_clause = "AND t.direction_id = ?" if eff_dir is not None else ""
            sql = f"""
                SELECT t.trip_id, r.route_short_name, st.departure_time
                FROM trips t
                JOIN routes r ON t.route_id = r.route_id
                JOIN agency a ON r.agency_id = a.agency_id
                JOIN stop_times st ON t.trip_id = st.trip_id
                WHERE r.route_short_name = ? AND a.agency_noc = ?
                {dir_clause}
                AND (
                    EXISTS (
                        SELECT 1 FROM calendar c
                        WHERE c.service_id=t.service_id
                          AND c.{day_col}=1
                          AND c.start_date<=? AND c.end_date>=?
                          AND NOT EXISTS (
                              SELECT 1 FROM calendar_dates removed
                              WHERE removed.service_id=t.service_id
                                AND removed.date=?
                                AND removed.exception_type=2
                          )
                    )
                    OR EXISTS (
                        SELECT 1 FROM calendar_dates added
                        WHERE added.service_id=t.service_id
                          AND added.date=? AND added.exception_type=1
                    )
                )
                AND st.stop_sequence = 1
                AND st.departure_time BETWEEN ? AND ?
            """
            params = [line_name, operator_noc]
            if eff_dir is not None:
                params.append(eff_dir)
            params.extend([
                date_str, date_str, date_str, date_str, lower, upper])
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                continue
            # nearest departure-time gap first (was: arbitrary LIMIT 1)
            def gap(row):
                secs = _gtfs_secs(row[2])
                return abs(secs - target_secs) if secs is not None else 10**9
            for row in sorted(rows, key=gap):
                schedule = _schedule_for(cur, row[0])
                if not schedule:
                    continue
                if vehicle_pos is not None and \
                        not _route_near(schedule, *vehicle_pos):
                    continue  # same line number, different town
                return Match(trip_id=row[0], route_short_name=row[1],
                             schedule=schedule, tier="fuzzy")
    return None


def match_vehicle(cur, operator_noc: str, line_name: str, direction_ref: str | None,
                  origin_local: datetime, journey_ref: str = "",
                  enable_exact: bool = False,
                  vehicle_pos: tuple[float, float] | None = None) -> Match | None:
    """The one entry point. Exact tier only when explicitly enabled."""
    if enable_exact:
        m = match_exact(
            cur, operator_noc, journey_ref, origin_local, line_name)
        if m:
            return m
    return match_fuzzy(cur, operator_noc, line_name, direction_ref, origin_local,
                       vehicle_pos=vehicle_pos)
