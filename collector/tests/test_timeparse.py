from datetime import datetime, timezone

import pytest
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


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Clocks go back at 01:00 UTC on Sunday 25 October 2026 and forward at
# 01:00 UTC on Sunday 29 March 2026. Timetable times are wall-clock.

@pytest.mark.parametrize("day,expected_utc", [
    ((2026, 10, 24), _utc(2026, 10, 24, 13, 0)),   # BST
    ((2026, 10, 25), _utc(2026, 10, 25, 14, 0)),   # clocks went back
    ((2026, 10, 26), _utc(2026, 10, 26, 14, 0)),   # GMT
    ((2026, 3, 28), _utc(2026, 3, 28, 14, 0)),     # GMT
    ((2026, 3, 29), _utc(2026, 3, 29, 13, 0)),     # clocks went forward
    ((2026, 3, 30), _utc(2026, 3, 30, 13, 0)),     # BST
])
def test_daytime_stop_is_wall_clock_on_and_around_both_changes(day, expected_utc):
    origin = datetime(*day, 14, 0, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("14:00:00"))
    sched = scheduled_local(sm, gtfs_seconds("14:00:00"))
    assert sched.astimezone(timezone.utc) == expected_utc
    assert sched == origin


@pytest.mark.parametrize("day", [(2026, 10, 25), (2026, 3, 29)])
def test_bus_on_time_by_the_clock_reads_zero_delay_on_change_day(day):
    # The 25 October regression: an on-time bus read as 60 minutes late.
    origin = datetime(*day, 7, 22, tzinfo=LDN)
    sm = service_midnight(origin, gtfs_seconds("07:22:00"))
    sched = scheduled_local(sm, gtfs_seconds("08:32:00"))
    recorded = datetime(*day, 8, 32, 37, tzinfo=LDN).astimezone(timezone.utc)
    assert int((recorded - sched).total_seconds()) == 37


def test_previous_service_day_past_midnight_crosses_the_autumn_change():
    # Saturday's service day continues past 24:00 into the changeover night.
    sm = datetime(2026, 10, 24, 0, 0, tzinfo=LDN)
    assert scheduled_local(sm, gtfs_seconds("24:30:00")).astimezone(
        timezone.utc) == _utc(2026, 10, 24, 23, 30)            # 00:30 BST
    assert scheduled_local(sm, gtfs_seconds("26:30:00")).astimezone(
        timezone.utc) == _utc(2026, 10, 25, 2, 30)             # 02:30 GMT


def test_change_day_service_past_midnight_lands_on_monday_gmt():
    sm = datetime(2026, 10, 25, 0, 0, tzinfo=LDN)
    sched = scheduled_local(sm, gtfs_seconds("24:30:00"))
    assert sched.astimezone(timezone.utc) == _utc(2026, 10, 26, 0, 30)


def test_trip_leaving_before_the_autumn_change_keeps_bst_in_repeated_hour():
    origin = datetime(2026, 10, 25, 0, 50, tzinfo=LDN)          # 23:50 UTC
    origin_utc = origin.astimezone(timezone.utc)
    sm = service_midnight(origin, gtfs_seconds("00:50:00"))
    times = [scheduled_local(sm, gtfs_seconds(t), origin_utc).astimezone(
        timezone.utc) for t in ("00:50:00", "01:30:00", "02:10:00")]
    assert times == [_utc(2026, 10, 24, 23, 50), _utc(2026, 10, 25, 0, 30),
                     _utc(2026, 10, 25, 2, 10)]


def test_trip_leaving_in_the_second_repeated_hour_stays_after_departure():
    origin = datetime(2026, 10, 25, 1, 40, fold=1, tzinfo=LDN)  # 01:40 GMT
    origin_utc = origin.astimezone(timezone.utc)
    assert origin_utc == _utc(2026, 10, 25, 1, 40)
    sm = service_midnight(origin, gtfs_seconds("01:40:00"))
    times = [scheduled_local(sm, gtfs_seconds(t), origin_utc).astimezone(
        timezone.utc) for t in ("01:40:00", "01:55:00", "02:20:00")]
    assert times == [_utc(2026, 10, 25, 1, 40), _utc(2026, 10, 25, 1, 55),
                     _utc(2026, 10, 25, 2, 20)]
    # Without the departure anchor the first occurrence (BST) is used.
    assert scheduled_local(sm, gtfs_seconds("01:55:00")).astimezone(
        timezone.utc) == _utc(2026, 10, 25, 0, 55)


def test_trip_spanning_the_spring_change():
    origin = datetime(2026, 3, 29, 0, 40, tzinfo=LDN)          # 00:40 GMT
    origin_utc = origin.astimezone(timezone.utc)
    sm = service_midnight(origin, gtfs_seconds("00:40:00"))
    times = [scheduled_local(sm, gtfs_seconds(t), origin_utc).astimezone(
        timezone.utc) for t in ("00:40:00", "00:55:00", "02:20:00")]
    assert times == [_utc(2026, 3, 29, 0, 40), _utc(2026, 3, 29, 0, 55),
                     _utc(2026, 3, 29, 1, 20)]


def test_scheduled_local_skips_the_missing_spring_hour():
    sm = datetime(2026, 3, 29, 0, 0, tzinfo=LDN)
    scheduled = scheduled_local(sm, gtfs_seconds("01:30:00"))
    assert scheduled == datetime(2026, 3, 29, 2, 30, tzinfo=LDN)


def test_previous_service_day_past_midnight_crosses_the_spring_change():
    sm = datetime(2026, 3, 28, 0, 0, tzinfo=LDN)
    assert scheduled_local(sm, gtfs_seconds("24:30:00")).astimezone(
        timezone.utc) == _utc(2026, 3, 29, 0, 30)              # 00:30 GMT
    assert scheduled_local(sm, gtfs_seconds("26:30:00")).astimezone(
        timezone.utc) == _utc(2026, 3, 29, 1, 30)              # 02:30 BST


@pytest.mark.parametrize("day,origin_hm,stop,expected_utc", [
    ((2026, 10, 25), (13, 0), "14:00:00", _utc(2026, 10, 25, 14, 0)),
    ((2026, 3, 29), (13, 0), "14:00:00", _utc(2026, 3, 29, 13, 0)),
    ((2026, 10, 24), (23, 50), "00:10:00", _utc(2026, 10, 24, 23, 10)),
])
def test_parse_schedule_time_on_change_days(day, origin_hm, stop, expected_utc):
    origin = datetime(*day, *origin_hm, tzinfo=LDN)
    sched = parse_schedule_time(stop, origin, LDN)
    assert sched.astimezone(timezone.utc) == expected_utc
