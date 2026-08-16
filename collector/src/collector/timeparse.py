"""Timezone-aware GTFS and SIRI time handling.

GTFS hours may exceed 24 for journeys continuing after midnight. Scheduled
times are therefore anchored to the service day rather than parsed as ordinary
clock times.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone, tzinfo as _tzinfo

from dateutil.parser import isoparse


def parse_iso_utc(timestamp_str: str | None) -> datetime | None:
    """ISO timestamp -> aware UTC datetime, or None. Naive input assumed UTC."""
    if not timestamp_str:
        return None
    try:
        dt = isoparse(timestamp_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def gtfs_seconds(gtfs_time_str: str | None) -> int | None:
    """'HH:MM[:SS]' -> seconds since service midnight. Accepts HH >= 24.

    Returns None on malformed input rather than raising: feed data is dirty
    and a bad time must never kill a poll cycle.
    """
    if not gtfs_time_str:
        return None
    try:
        p = str(gtfs_time_str).split(":")
        h, m = int(p[0]), int(p[1])
        s = int(p[2]) if len(p) > 2 else 0
        if h < 0 or not (0 <= m <= 59 and 0 <= s <= 59):
            return None
        return h * 3600 + m * 60 + s
    except (ValueError, TypeError, IndexError):
        return None


def service_midnight(origin_local: datetime, first_stop_secs: int) -> datetime:
    """The service day's midnight, anchored on the trip's own first-stop offset.

    origin_local is the journey's scheduled first departure (aware, local tz);
    first_stop_secs is that same moment as the GTFS offset (may be >= 86400
    for trips whose timetable starts past 24:00). Subtracting one from the
    other lands on the correct calendar day even across the date line of the
    service day.
    """
    day_offset = first_stop_secs // 86400
    service_date = origin_local.date() - timedelta(days=day_offset)
    return datetime.combine(service_date, time.min, tzinfo=origin_local.tzinfo)


def scheduled_local(service_midnight_dt: datetime, stop_secs: int) -> datetime:
    """Absolute aware local datetime of a GTFS service-day offset.

    GTFS time is elapsed time from the service-day origin. Advancing on the UTC
    timeline makes the repeated autumn hour unambiguous and skips the missing
    spring hour instead of silently keeping midnight's UTC offset.
    """
    if service_midnight_dt.tzinfo is None:
        return service_midnight_dt + timedelta(seconds=stop_secs)
    return (service_midnight_dt.astimezone(timezone.utc)
            + timedelta(seconds=stop_secs)).astimezone(
                service_midnight_dt.tzinfo)


def parse_schedule_time(schedule_time_str: str | None,
                        origin_departure_local: datetime | None,
                        target_tz: _tzinfo) -> datetime | None:
    """app.py's parse_schedule_time_py, behaviour preserved.

    Resolves a GTFS 'HH:MM:SS' to an absolute aware datetime relative to the
    journey's origin departure. Quirk preserved deliberately: a same-day time
    EARLIER than the origin time is pushed to the next day (a bus cannot be
    scheduled at a stop before it left its origin).
    """
    if not schedule_time_str or origin_departure_local is None \
            or origin_departure_local.tzinfo is None:
        return None
    secs = gtfs_seconds(schedule_time_str)
    if secs is None:
        return None
    day_offset, rem = divmod(secs, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)

    from datetime import time as time_obj
    naive_stop_time = time_obj(hour, minute, second)
    if day_offset == 0 and naive_stop_time < origin_departure_local.time():
        day_offset = 1

    stop_date = origin_departure_local.date() + timedelta(days=day_offset)
    return datetime.combine(stop_date, naive_stop_time, tzinfo=target_tz)
