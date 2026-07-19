"""Provide map stops and scheduled departures from GTFS data."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .stop_names import clean_stop_name

LDN = ZoneInfo("Europe/London")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# WECA bounding box for the stop query
BBOX = (51.2731, 51.6773, -3.1151, -2.2521)

# Operators whose scheduled departures the board shows;
# widen when the display allowlist decision is revisited.
SCHEDULED_NOCS = ("FBRI", "BFBC", "LEMB")


def all_stops(gtfs_conn) -> list[dict]:
    rows = gtfs_conn.execute(
        """SELECT stop_code, stop_name, stop_lat, stop_lon FROM stops
           WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
             AND stop_lat IS NOT NULL AND stop_lon IS NOT NULL""",
        BBOX).fetchall()
    return [{
        "stop_code": r["stop_code"],
        "common_name": clean_stop_name(r["stop_name"], r["stop_code"] or ""),
        "latitude": r["stop_lat"],
        "longitude": r["stop_lon"],
    } for r in rows]


def scheduled_departures(gtfs_conn, stop_code: str,
                         now_local: datetime | None = None) -> dict | None:
    stop = gtfs_conn.execute(
        "SELECT stop_id, stop_code, stop_name FROM stops WHERE stop_code = ?",
        (stop_code,)).fetchone()
    if not stop:
        return None
    now = now_local or datetime.now(LDN)
    today = now.strftime("%Y%m%d")
    day_col = DAYS[now.weekday()]  # from a fixed list, never user input
    now_gtfs = now.strftime("%H:%M:%S")
    end = now + timedelta(minutes=90)
    if end.date() > now.date():  # GTFS convention: past-midnight = hour+24
        end_gtfs = f"{end.hour + 24:02d}:{end.minute:02d}:{end.second:02d}"
    else:
        end_gtfs = end.strftime("%H:%M:%S")

    nocs = ",".join("?" * len(SCHEDULED_NOCS))
    sql = f"""
        SELECT DISTINCT r.route_short_name AS line,
               t.trip_headsign AS destination, st.departure_time
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN agency a ON r.agency_id = a.agency_id
        JOIN calendar c ON t.service_id = c.service_id
        WHERE st.stop_id = ? AND a.agency_noc IN ({nocs})
          AND c.{day_col} = 1 AND c.start_date <= ? AND c.end_date >= ?
          AND st.departure_time >= ? AND st.departure_time <= ?
          AND t.service_id NOT IN (SELECT service_id FROM calendar_dates
                                   WHERE date = ? AND exception_type = 2)
        UNION
        SELECT DISTINCT r.route_short_name, t.trip_headsign, st.departure_time
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN agency a ON r.agency_id = a.agency_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        WHERE st.stop_id = ? AND a.agency_noc IN ({nocs})
          AND cd.date = ? AND cd.exception_type = 1
          AND st.departure_time >= ? AND st.departure_time <= ?
        ORDER BY departure_time ASC LIMIT 20
    """
    rows = gtfs_conn.execute(sql, (
        stop["stop_id"], *SCHEDULED_NOCS, today, today, now_gtfs, end_gtfs,
        today, stop["stop_id"], *SCHEDULED_NOCS, today, now_gtfs, end_gtfs,
    )).fetchall()

    departures = []
    for r in rows:
        dep_dt = _gtfs_time_today(r["departure_time"], now)
        if dep_dt is None:
            continue
        eta_mins = math.ceil((dep_dt - now).total_seconds() / 60.0)
        if eta_mins >= -2:
            departures.append({
                "line": r["line"] or "",
                "destination": r["destination"] or "Unknown",
                "scheduled_time": dep_dt.strftime("%H:%M"),
                "eta_mins": eta_mins,
                "source": "scheduled",
            })
    return {
        "stop_code": stop_code,
        "stop_name": clean_stop_name(stop["stop_name"] or stop_code, stop_code),
        "scheduled_departures": departures,
        "last_updated": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def _gtfs_time_today(time_str: str, now_local: datetime) -> datetime | None:
    """Anchor a GTFS time, including values beyond 24:00, to today."""
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        days, h = divmod(h, 24)
        base = now_local.replace(hour=h, minute=m, second=s, microsecond=0)
        return base + timedelta(days=days)
    except (ValueError, TypeError, AttributeError):
        return None
