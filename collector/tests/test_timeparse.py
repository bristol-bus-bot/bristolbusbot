from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from collector.timeparse import (gtfs_seconds, parse_iso_utc, parse_schedule_time,
                                 scheduled_local, service_day_origin_utc,
                                 service_midnight)

LDN = ZoneInfo("Europe/London")


def test_gtfs_seconds_ordinary():
    assert gtfs_seconds("08:30:00") == 8 * 3600 + 30 * 60


def test_gtfs_seconds_past_midnight():
    # 25:30 means 01:30 next day: must NOT wrap or raise
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


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Clocks go back at 01:00 UTC on Sunday 25 October 2026 and forward at
# 01:00 UTC on Sunday 29 March 2026. GTFS times count from noon minus 12h.

@pytest.mark.parametrize("day,expected_utc", [
    ((2026, 10, 24), _utc(2026, 10, 24, 13, 0)),   # BST
    ((2026, 10, 25), _utc(2026, 10, 25, 14, 0)),   # clocks went back
    ((2026, 10, 26), _utc(2026, 10, 26, 14, 0)),   # GMT
    ((2026, 3, 28), _utc(2026, 3, 28, 14, 0)),     # GMT
    ((2026, 3, 29), _utc(2026, 3, 29, 13, 0)),     # clocks went forward
    ((2026, 3, 30), _utc(2026, 3, 30, 13, 0)),     # BST
])
def test_daytime_stop_is_on_the_clock_on_and_around_both_changes(day, expected_utc):
    origin = datetime(*day, 14, 0, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("14:00:00"))
    sched = scheduled_local(sm, gtfs_seconds("14:00:00"))
    assert sched.astimezone(timezone.utc) == expected_utc
    assert sched == origin


@pytest.mark.parametrize("day", [(2026, 10, 25), (2026, 3, 29)])
def test_bus_on_time_by_the_clock_reads_its_real_delay_on_change_day(day):
    # FBRI-30014 on route 75, 25 October 2026: 37 s late at Turtlegate
    # Avenue (08:32:00). Production read this as 61 minutes late.
    origin = datetime(*day, 7, 22, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("07:22:00"))
    sched = scheduled_local(sm, gtfs_seconds("08:32:00"))
    recorded = datetime(*day, 8, 32, 37, tzinfo=LDN).astimezone(timezone.utc)
    assert int((recorded - sched).total_seconds()) == 37


@pytest.mark.parametrize("day,origin_utc", [
    ((2026, 6, 9), _utc(2026, 6, 8, 23, 0)),
    ((2026, 10, 24), _utc(2026, 10, 23, 23, 0)),
    ((2026, 10, 25), _utc(2026, 10, 25, 0, 0)),
    ((2026, 3, 29), _utc(2026, 3, 28, 23, 0)),
    ((2026, 12, 1), _utc(2026, 12, 1, 0, 0)),
])
def test_service_day_origin_is_noon_minus_twelve_hours(day, origin_utc):
    assert service_day_origin_utc(datetime(*day, tzinfo=LDN)) == origin_utc


def test_overnight_trip_through_the_autumn_change_keeps_its_running_time():
    # Route 4, Saturday 24 October's service day, observed 1 s late at
    # 03:23:01 UTC at a stop timetabled 28:23:00 (production data).
    origin = datetime(2026, 10, 25, 2, 42, tzinfo=LDN)          # 27:42:00
    sm = service_midnight(origin, gtfs_seconds("27:42:00"))
    assert sm.date() == datetime(2026, 10, 24).date()
    times = [scheduled_local(sm, gtfs_seconds(t)).astimezone(timezone.utc)
             for t in ("24:50:00", "25:30:00", "26:10:00", "27:42:00",
                       "28:23:00")]
    assert times == [_utc(2026, 10, 24, 23, 50), _utc(2026, 10, 25, 0, 30),
                     _utc(2026, 10, 25, 1, 10), _utc(2026, 10, 25, 2, 42),
                     _utc(2026, 10, 25, 3, 23)]
    assert times == sorted(times)
    recorded = _utc(2026, 10, 25, 3, 23, 1)
    assert int((recorded - times[-1]).total_seconds()) == 1


def test_change_day_service_past_midnight_lands_on_monday_gmt():
    sm = datetime(2026, 10, 25, 0, 0, tzinfo=LDN)
    sched = scheduled_local(sm, gtfs_seconds("24:30:00"))
    assert sched.astimezone(timezone.utc) == _utc(2026, 10, 26, 0, 30)


def test_overnight_trip_through_the_spring_change_keeps_its_running_time():
    sm = datetime(2026, 3, 28, 0, 0, tzinfo=LDN)
    times = [scheduled_local(sm, gtfs_seconds(t)).astimezone(timezone.utc)
             for t in ("24:40:00", "25:30:00", "26:20:00")]
    assert times == [_utc(2026, 3, 29, 0, 40), _utc(2026, 3, 29, 1, 30),
                     _utc(2026, 3, 29, 2, 20)]
    assert scheduled_local(sm, gtfs_seconds("25:30:00")) == datetime(
        2026, 3, 29, 2, 30, tzinfo=LDN)


def test_change_day_service_past_midnight_after_spring_change():
    sm = datetime(2026, 3, 29, 0, 0, tzinfo=LDN)
    sched = scheduled_local(sm, gtfs_seconds("24:30:00"))
    assert sched == datetime(2026, 3, 30, 0, 30, tzinfo=LDN)


@pytest.mark.parametrize("day,origin_hm,stop,expected_utc", [
    ((2026, 10, 25), (13, 0), "14:00:00", _utc(2026, 10, 25, 14, 0)),
    ((2026, 3, 29), (13, 0), "14:00:00", _utc(2026, 3, 29, 13, 0)),
    ((2026, 10, 24), (23, 50), "00:10:00", _utc(2026, 10, 24, 23, 10)),
    ((2026, 6, 9), (23, 50), "00:10:00", _utc(2026, 6, 9, 23, 10)),
])
def test_parse_schedule_time_on_change_days(day, origin_hm, stop, expected_utc):
    origin = datetime(*day, *origin_hm, tzinfo=LDN)
    sched = parse_schedule_time(stop, origin, LDN)
    assert sched.astimezone(timezone.utc) == expected_utc
