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


def service_day_origin_utc(service_midnight_dt: datetime) -> datetime:
    """The instant GTFS times on this service day count from: noon minus 12h.

    This is local midnight except on a clock-change day. On 25 October it is
    00:00 UTC (01:00 BST), so '14:00:00' is 14:00 GMT; on 29 March it is
    23:00 UTC the day before, so '14:00:00' is 14:00 BST.
    """
    tz = service_midnight_dt.tzinfo
    noon = datetime.combine(service_midnight_dt.date(), time(12), tzinfo=tz)
    return noon.astimezone(timezone.utc) - timedelta(hours=12)


def scheduled_local(service_midnight_dt: datetime, stop_secs: int) -> datetime:
    """Absolute aware local datetime of a GTFS service-day offset.

    GTFS times are elapsed time from noon minus 12h on the service day, not
    from local midnight. That keeps daytime times on the clock on a change
    day, and keeps a journey that runs through the change at its real
    running time ('28:23:00' on Saturday 24 October is 03:23 GMT).
    """
    if service_midnight_dt.tzinfo is None:
        return service_midnight_dt + timedelta(seconds=stop_secs)
    return (service_day_origin_utc(service_midnight_dt)
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
    if not schedule_time_str or origin_departure_local is None             or origin_departure_local.tzinfo is None:
        return None
    secs = gtfs_seconds(schedule_time_str)
    if secs is None:
        return None
    clock = secs % 86400
    origin_clock = (origin_departure_local.hour * 3600
                    + origin_departure_local.minute * 60
                    + origin_departure_local.second)
    if secs < 86400 and clock < origin_clock:
        secs += 86400
    service_day = datetime.combine(
        origin_departure_local.date(), time.min, tzinfo=target_tz)
    return scheduled_local(service_day, secs)


def clock_change_offset_s(service_midnight_dt: datetime, stop_secs: int) -> int:
    """How far the clock reading of a GTFS time is from the standard one.

    Zero except overnight on a clock-change service day, where some
    journeys run to the printed clock time instead ('26:14:00' on Saturday
    24 October is 01:14 UTC by the standard, 02:14 GMT on the clock).
    """
    tz = service_midnight_dt.tzinfo
    if tz is None:
        return 0
    day_offset, rem = divmod(stop_secs, 86400)
    clock = datetime.combine(
        service_midnight_dt.date() + timedelta(days=day_offset), time.min,
        tzinfo=tz) + timedelta(seconds=rem)
    standard = scheduled_local(service_midnight_dt, stop_secs)
    return int((clock.astimezone(timezone.utc)
                - standard.astimezone(timezone.utc)).total_seconds())
