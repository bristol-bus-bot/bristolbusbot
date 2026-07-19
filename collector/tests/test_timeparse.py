from datetime import datetime
from zoneinfo import ZoneInfo

from collector.timeparse import (gtfs_seconds, parse_iso_utc, parse_schedule_time,
                                 scheduled_local, service_midnight)

LDN = ZoneInfo("Europe/London")


def test_gtfs_seconds_ordinary():
    assert gtfs_seconds("08:30:00") == 8 * 3600 + 30 * 60


def test_gtfs_seconds_past_midnight():
    # 25:30 means 01:30 next day — must NOT wrap or raise
    assert gtfs_seconds("25:30:00") == 25 * 3600 + 30 * 60


def test_gtfs_seconds_garbage():
    assert gtfs_seconds("not a time") is None
    assert gtfs_seconds("12:99:00") is None
    assert gtfs_seconds("") is None
    assert gtfs_seconds(None) is None


def test_service_midnight_anchors_on_first_stop():
    # Trip whose GTFS timetable starts at 23:50 (86400-600 s would be next-day
    # encoding; here plain 23:50): origin 23:50 local -> service day is that date
    origin = datetime(2026, 6, 9, 23, 50, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("23:50:00"))
    assert sm == datetime(2026, 6, 9, 0, 0, tzinfo=LDN)


def test_service_midnight_for_after_midnight_trip():
    # Trip starting 25:30 (01:30 on the 10th, service day the 9th)
    origin = datetime(2026, 6, 10, 1, 30, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("25:30:00"))
    assert sm == datetime(2026, 6, 9, 0, 0, tzinfo=LDN)


def test_scheduled_local_past_24h():
    sm = datetime(2026, 6, 9, 0, 0, tzinfo=LDN)
    sched = scheduled_local(sm, gtfs_seconds("25:30:00"))
    assert sched == datetime(2026, 6, 10, 1, 30, tzinfo=LDN)


def test_parse_schedule_time_next_day_quirk():
    # Stop time earlier than origin time on day 0 -> pushed to next day
    origin = datetime(2026, 6, 9, 23, 50, tzinfo=LDN)
    sched = parse_schedule_time("00:10:00", origin, LDN)
    assert sched == datetime(2026, 6, 10, 0, 10, tzinfo=LDN)


def test_parse_iso_utc_naive_assumed_utc():
    dt = parse_iso_utc("2026-06-09T12:00:00")
    assert dt.tzinfo is not None and dt.hour == 12


def test_bst_offset_is_respected():
    # June: Europe/London is UTC+1. A 12:00 local schedule vs 12:00Z recorded
    # must show one hour of delay, not zero.
    sm = datetime(2026, 6, 9, 0, 0, tzinfo=LDN)
    sched = scheduled_local(sm, gtfs_seconds("12:00:00"))
    recorded = parse_iso_utc("2026-06-09T12:00:00Z")
    assert int((recorded - sched).total_seconds()) == 3600
