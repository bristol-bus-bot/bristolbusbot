"""Build the stop schedule shown for a selected vehicle journey."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_JOURNEY_AGE = timedelta(hours=2)

from collector.matching import DAYS, match_fuzzy
from collector.timeparse import gtfs_seconds, parse_iso_utc

from .stop_names import clean_stop_name

LDN = ZoneInfo("Europe/London")

# out-of-WECA stop-code prefixes -> displayed area name
_PREFIX_AREAS = [("sot", "Somerset"), ("gwe", "Monmouthshire"),
                 ("Wil", "Wiltshire"), ("glc", "Gloucestershire")]

_SCHEDULE_SQL = """
    SELECT st.stop_sequence, st.arrival_time, st.departure_time,
           s.stop_code, s.stop_name, s.stop_lat, s.stop_lon
    FROM stop_times st JOIN stops s ON st.stop_id = s.stop_id
    WHERE st.trip_id = ? ORDER BY st.stop_sequence ASC
"""


def _rows_for_trip(gtfs, trip_id: str) -> list:
    return gtfs.execute(_SCHEDULE_SQL, (trip_id,)).fetchall()


def _exact(gtfs, journey_code: str, operator: str | None) -> str | None:
    op_clause = "AND a.agency_noc = ?" if operator else ""
    params = [journey_code] + ([operator] if operator else [])
    row = gtfs.execute(f"""
        SELECT t.trip_id FROM trips t
        JOIN routes r ON t.route_id = r.route_id
        JOIN agency a ON r.agency_id = a.agency_id
        WHERE t.vehicle_journey_code = ? {op_clause} LIMIT 1""",
        params).fetchone()
    return row["trip_id"] if row else None


def _by_route_now(gtfs, line: str, direction_ref: str, operator: str | None,
                  now_local: datetime) -> str | None:
    """Nearest active trip on this line/direction around ``now_local``.

    Both today's and yesterday's service days are considered because GTFS
    represents post-midnight trips as 24:xx/25:xx on the previous service
    day. Calendar exceptions are applied before choosing the nearest absolute
    departure time.
    """
    direction_id = {"outbound": 0, "inbound": 1}.get(
        (direction_ref or "").lower().strip())
    dir_clause = "AND t.direction_id = ?" if direction_id is not None else ""
    op_clause = "AND a.agency_noc = ?" if operator else ""
    best, best_gap = None, None
    for service_day in (now_local.date(), now_local.date() - timedelta(days=1)):
        service_ids = _active_services(gtfs, service_day)
        if not service_ids:
            continue
        placeholders = ",".join("?" for _ in service_ids)
        params: list = [line]
        if operator:
            params.append(operator)
        if direction_id is not None:
            params.append(direction_id)
        params.extend(sorted(service_ids))
        rows = gtfs.execute(f"""
            SELECT t.trip_id, st.departure_time FROM trips t
            JOIN routes r ON t.route_id = r.route_id
            JOIN agency a ON r.agency_id = a.agency_id
            JOIN stop_times st ON st.trip_id = t.trip_id AND st.stop_sequence = 1
            WHERE r.route_short_name = ? {op_clause} {dir_clause}
              AND t.service_id IN ({placeholders})""", params).fetchall()
        service_midnight = datetime.combine(
            service_day, datetime.min.time(), tzinfo=LDN)
        for row in rows:
            seconds = gtfs_seconds(row["departure_time"])
            if seconds is None:
                continue
            departure = service_midnight + timedelta(seconds=seconds)
            gap = abs((departure - now_local).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = row["trip_id"], gap

    # Reject route-only matches that are too far from the current time.
    return best if best_gap is not None and best_gap <= MAX_JOURNEY_AGE.total_seconds() else None


def _active_services(gtfs, service_day: date) -> set[str]:
    date_text = service_day.strftime("%Y%m%d")
    day_col = DAYS[service_day.weekday()]
    active = {row[0] for row in gtfs.execute(
        f"SELECT service_id FROM calendar WHERE {day_col}=1 "
        "AND start_date <= ? AND end_date >= ?", (date_text, date_text))}
    for service_id, exception_type in gtfs.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date=?",
            (date_text,)):
        if exception_type == 1:
            active.add(service_id)
        elif exception_type == 2:
            active.discard(service_id)
    return active


def journey_schedule(gtfs, journey_code: str, *, trip_id: str = "",
                     operator: str = "",
                     line: str = "", direction_ref: str = "",
                     origin_aimed_dep: str = "", locality_map: dict | None = None,
                     now_local: datetime | None = None) -> dict | None:
    now = now_local or datetime.now(LDN)

    # Do not present an expired journey as current.
    if origin_aimed_dep:
        origin_utc = parse_iso_utc(origin_aimed_dep)
        if origin_utc and (now.astimezone(timezone.utc) - origin_utc) > MAX_JOURNEY_AGE:
            return None

    # Prefer the collector's trip match when the caller provides it.
    if not trip_id:
        trip_id = _exact(gtfs, journey_code, operator or None) \
            or _exact(gtfs, journey_code, None)

    if not trip_id and line and origin_aimed_dep:
        origin_utc = parse_iso_utc(origin_aimed_dep)
        if origin_utc:
            origin_local = origin_utc.astimezone(LDN)
            m = (match_fuzzy(gtfs.cursor(), operator, line, direction_ref,
                             origin_local) if operator else None) \
                or _fuzzy_any_operator(gtfs, line, direction_ref, origin_local)
            trip_id = m.trip_id if m else None

    if not trip_id and line and not origin_aimed_dep:
        trip_id = _by_route_now(gtfs, line, direction_ref, operator or None, now)

    if not trip_id:
        return None

    rows = _rows_for_trip(gtfs, trip_id)
    if not rows:
        return None
    loc = locality_map or {}
    stops = []
    for r in rows:
        code = r["stop_code"] or ""
        entry = loc.get(code, {})
        ward, area = entry.get("ward", ""), entry.get("area", "")
        if not ward or ward == "Other":
            for prefix, name in _PREFIX_AREAS:
                if code.startswith(prefix):
                    area = name
                    break
        stops.append({
            "stop_code": code,
            "common_name": clean_stop_name(r["stop_name"] or "Unknown", code),
            "arrival_time": r["arrival_time"],
            "departure_time": r["departure_time"],
            "latitude": r["stop_lat"],
            "longitude": r["stop_lon"],
            "ward": ward or area or "Other",
            "area": area or "Other",
            "stop_sequence": r["stop_sequence"] or 0,
        })
    return {"stops": stops,
            "destination": stops[-1]["common_name"] if stops else None}


def _fuzzy_any_operator(gtfs, line, direction_ref, origin_local):
    """Find a display-only fuzzy match when the operator is unavailable."""
    ops = [r[0] for r in gtfs.execute(
        "SELECT DISTINCT agency_noc FROM agency WHERE agency_noc IS NOT NULL")]
    for op in ops:
        m = match_fuzzy(gtfs.cursor(), op, line, direction_ref, origin_local)
        if m:
            return m
    return None
