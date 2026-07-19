"""Build live departure estimates for a stop from matched vehicle data."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .stop_names import clean_stop_name

LDN = ZoneInfo("Europe/London")
ETA_MIN, ETA_MAX = -2, 60
MAX_RESULTS = 10

# Select calls that are still ahead of each matched vehicle. GTFS times are
# handled in Python to support values beyond 24:00.
_QUERY = """
SELECT v.vehicle_ref, v.line, v.destination, v.delay_seconds,
       v.origin_aimed_departure, v.stop_code AS current_stop_code,
       v.stop_sequence AS current_seq, v.distance_m,
       st.arrival_time, st.departure_time, st.stop_sequence AS target_seq
FROM vehicles v
JOIN stop_times st ON st.trip_id = v.trip_id
JOIN stops s ON s.stop_id = st.stop_id
WHERE v.updated_at > :cutoff
  AND v.trip_id IS NOT NULL
  AND v.delay_seconds IS NOT NULL
  AND s.stop_code = :stop_code
  AND st.stop_sequence > v.stop_sequence
"""


def departures_for_stop(live_conn, gtfs_conn, stop_code: str,
                        stale_seconds: int = 90,
                        now_utc: datetime | None = None) -> dict | None:
    stop = gtfs_conn.execute(
        "SELECT stop_code, stop_name FROM stops WHERE stop_code = ?",
        (stop_code,)).fetchone()
    if not stop:
        return None
    now = now_utc or datetime.now(timezone.utc)
    now_local = now.astimezone(LDN)

    # The databases are separate files, so the query uses a read-only ATTACH.
    live_conn.execute("ATTACH DATABASE ? AS gtfs",
                      (_db_path(gtfs_conn),))
    try:
        rows = live_conn.execute(
            _QUERY.replace("stop_times", "gtfs.stop_times")
                  .replace("JOIN stops s", "JOIN gtfs.stops s"),
            {"cutoff": (now - timedelta(seconds=stale_seconds)).isoformat(),
             "stop_code": stop_code}).fetchall()
    finally:
        live_conn.execute("DETACH DATABASE gtfs")

    departures = []
    for r in rows:
        eta = _eta(r, now_local)
        if eta is None:
            continue
        eta_dt_local, eta_mins = eta
        if not (ETA_MIN <= eta_mins <= ETA_MAX):
            continue
        current_name = _stop_name(gtfs_conn, r["current_stop_code"])
        departures.append({
            "line": r["line"],
            "destination": r["destination"] or "Unknown",
            "eta_mins": eta_mins,
            "eta_dt_iso": eta_dt_local.isoformat(),
            "current_stop": current_name or "Unknown",
            "distance_from_route": r["distance_m"],
            "source": "live",
            "vehicleRef": r["vehicle_ref"],
        })

    departures.sort(key=lambda d: d["eta_dt_iso"])
    return {
        "stop_code": stop_code,
        "stop_name": clean_stop_name(stop["stop_name"] or stop_code, stop_code),
        "departures": departures[:MAX_RESULTS],
        "last_updated": now_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def _eta(row, now_local: datetime):
    """Scheduled arrival (>24:00-aware, anchored on the journey's origin)
    plus the vehicle's current observed delay."""
    from collector.timeparse import parse_iso_utc, parse_schedule_time
    origin_local_utc = parse_iso_utc(row["origin_aimed_departure"])
    if origin_local_utc is None:
        return None
    origin_local = origin_local_utc.astimezone(LDN)
    sched_str = row["arrival_time"] or row["departure_time"]
    sched_local = parse_schedule_time(sched_str, origin_local, LDN)
    if sched_local is None:
        return None
    eta_dt = sched_local + timedelta(seconds=row["delay_seconds"])
    eta_mins = math.ceil((eta_dt - now_local).total_seconds() / 60.0)
    return eta_dt, eta_mins


def _stop_name(gtfs_conn, stop_code: str | None) -> str | None:
    if not stop_code:
        return None
    row = gtfs_conn.execute(
        "SELECT stop_name FROM stops WHERE stop_code = ?", (stop_code,)).fetchone()
    return clean_stop_name(row["stop_name"], stop_code) if row else None


def _db_path(conn) -> str:
    for _, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return path
    raise RuntimeError("cannot resolve database path")
