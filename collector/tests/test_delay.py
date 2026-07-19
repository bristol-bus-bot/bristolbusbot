from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from collector.delay import (LOW_CONFIDENCE_DISTANCE_M, classify_event,
                             closest_stop, live_estimate, settled_reading)
from collector.timeparse import gtfs_seconds, service_midnight

LDN = ZoneInfo("Europe/London")

# A tiny schedule: three stops heading south through Bristol.
# (seq, departure_time, timepoint, stop_code, lat, lon)
SCHEDULE = [
    (1, "11:15:00", 1, "0100A", 51.4600, -2.5890, "Origin"),
    (2, "11:20:00", 0, "0100B", 51.4550, -2.5890, "Middle"),
    (3, "11:25:00", 1, "0100C", 51.4500, -2.5890, "End"),
]
SM = service_midnight(datetime(2026, 6, 9, 11, 15, tzinfo=LDN),
                      gtfs_seconds("11:15:00"))


def utc(h, m, s=0):
    return datetime(2026, 6, 9, h, m, s, tzinfo=timezone.utc)


def test_closest_stop_picks_nearest():
    cs = closest_stop(51.4551, -2.5890, SCHEDULE)
    assert cs.row[3] == "0100B"
    assert cs.distance_m < 20


def test_live_estimate_on_time():
    # At stop B exactly at its scheduled moment (11:20 BST = 10:20 UTC)
    est = live_estimate(51.4550, -2.5890, utc(10, 20), SCHEDULE, SM)
    assert est is not None
    assert est.delay_s == 0
    assert est.event_type == "punctual"
    assert not est.low_confidence


def test_live_estimate_late_and_flagged_far():
    # ~500 m east of stop B, 6 minutes after its scheduled time
    est = live_estimate(51.4550, -2.5818, utc(10, 26), SCHEDULE, SM)
    assert est is not None
    assert est.delay_s == 360
    assert est.event_type == "delayed"
    assert est.low_confidence  # beyond LOW_CONFIDENCE_DISTANCE_M
    assert est.distance_m > LOW_CONFIDENCE_DISTANCE_M


def test_live_estimate_none_beyond_gate():
    # ~2 km away: no estimate at all
    est = live_estimate(51.4550, -2.5600, utc(10, 20), SCHEDULE, SM)
    assert est is None


def test_settled_reading_requires_timing_point():
    # Stop B is timepoint=0 -> no settled reading there...
    r = settled_reading(51.4550, -2.5890, utc(10, 20), SCHEDULE, SM)
    assert r is None
    # ...but stop C (timepoint=1) yields one
    r = settled_reading(51.4500, -2.5890, utc(10, 27), SCHEDULE, SM)
    assert r is not None
    assert r.stop_code == "0100C"
    assert r.observed_delay_s == 120
    assert r.on_time  # +120 s is inside the DfT band


def test_settled_reading_dft_band_edges():
    # 5 min 59 s late = on time; 6 min 00 s = not
    r_359 = settled_reading(51.4500, -2.5890, utc(10, 30, 59), SCHEDULE, SM)
    r_360 = settled_reading(51.4500, -2.5890, utc(10, 31, 0), SCHEDULE, SM)
    assert r_359.on_time and not r_360.on_time
    # exactly 1 min early = on time; beyond = not
    r_m60 = settled_reading(51.4500, -2.5890, utc(10, 24, 0), SCHEDULE, SM)
    r_m61 = settled_reading(51.4500, -2.5890, utc(10, 23, 59), SCHEDULE, SM)
    assert r_m60.on_time and not r_m61.on_time


def test_sanity_band_drops_impossible():
    # 2 hours "late" is outside the storage band -> dropped, not stored
    est = live_estimate(51.4550, -2.5890, utc(12, 20), SCHEDULE, SM)
    assert est is None


def test_classify_event_thresholds():
    assert classify_event(4 * 60) == "delayed"
    assert classify_event(4 * 60 - 1) == "punctual"
    assert classify_event(-3 * 60) == "early"
    assert classify_event(-3 * 60 + 1) == "punctual"


def test_live_estimate_carries_stop_name():
    est = live_estimate(51.4550, -2.5890, utc(10, 20), SCHEDULE, SM)
    assert est.stop_name == "Middle"
