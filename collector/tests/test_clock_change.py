from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from collector import audit_db
from collector.clock_change import ClockChangeTrips
from collector.delay import live_estimate_result, settled_reading_result
from collector.run import _apply_clock_change
from collector.timeparse import clock_change_offset_s, gtfs_seconds

LDN = ZoneInfo("Europe/London")


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize("day,stop,offset", [
    ((2026, 10, 23), "14:00:00", 0),
    ((2026, 10, 23), "25:30:00", 0),
    ((2026, 10, 24), "25:30:00", 0),       # before the change: both agree
    ((2026, 10, 24), "26:14:00", 3600),    # 01:14 UTC standard, 02:14 GMT clock
    ((2026, 10, 24), "28:23:00", 3600),
    ((2026, 10, 25), "00:30:00", -3600),
    ((2026, 10, 25), "14:00:00", 0),
    ((2026, 3, 28), "24:30:00", 0),
    ((2026, 3, 28), "26:30:00", -3600),
    ((2026, 3, 29), "00:30:00", 3600),
    ((2026, 3, 29), "14:00:00", 0),
])
def test_clock_offset_is_zero_except_overnight_on_change_days(day, stop, offset):
    midnight = datetime(*day, tzinfo=LDN)
    assert clock_change_offset_s(midnight, gtfs_seconds(stop)) == offset


def test_journey_waits_for_two_readings_then_follows_the_evidence():
    trips = ClockChangeTrips()
    # A1 26:05 (FBRI-30151): an hour out on the standard, on time by the clock.
    assert trips.observe("20261024", "A1", 3646, 3600) == "pending"
    assert trips.observe("20261024", "A1", 3650, 3600) == "clock"
    # Route 4 27:42 (FBRI-39411): on time on the standard.
    assert trips.observe("20261024", "R4", -35, 3600) == "pending"
    assert trips.observe("20261024", "R4", 1, 3600) == "standard"
    # Genuinely 40 minutes late on the standard: 20 minutes early by the
    # clock is not normal either, so the standard reading stands.
    trips.observe("20261024", "LATE", 2400, 3600)
    assert trips.observe("20261024", "LATE", 2460, 3600) == "standard"
    # Readings with no offset never start a decision.
    assert trips.observe("20261023", "DAY", 3600, 0) == "standard"


def test_only_the_latest_two_service_days_are_kept():
    trips = ClockChangeTrips()
    trips.observe("20261024", "A", 3600, 3600)
    trips.observe("20261025", "B", 3600, -3600)
    trips.observe("20261026", "C", 3600, 3600)
    assert trips.decision("20261024", "A") == "standard"
    assert trips.decision("20261025", "B") == "pending"


# Two timing points on one overnight journey (seq, time, timepoint, code, lat, lon).
def _journey(first, second):
    return SimpleNamespace(trip_id="TRIP", schedule=[
        (0, first, 1, "S0", 51.4500, -2.5800, "First"),
        (10, second, 1, "S1", 51.4700, -2.5800, "Second"),
    ])


def _poll(match, service_day, lat, recorded, trips, audit):
    midnight = datetime(*service_day, tzinfo=LDN)
    est, est_reason = live_estimate_result(
        lat, -2.5800, recorded, match.schedule, midnight)
    reading, reading_reason = settled_reading_result(
        lat, -2.5800, recorded, match.schedule, midnight)
    service_date = midnight.strftime("%Y%m%d")
    est, reading, est_reason, reading_reason = _apply_clock_change(
        trips, service_date, match, est, reading, est_reason, reading_reason,
        audit.cursor(), LDN)
    if reading is not None:
        audit_db.upsert_observation(audit.cursor(), (
            service_date, "FBRI", "A1", match.trip_id, "2605",
            reading.stop_sequence, reading.stop_code,
            reading.scheduled_local.isoformat(), reading.observed_delay_s,
            int(reading.on_time), reading.gps_distance_m, recorded.isoformat(),
            "FBRI-30151", int(reading.is_origin), "fuzzy"))
    return est, est_reason


def _stored(audit):
    return audit.execute(
        "SELECT stop_sequence, observed_delay_s, on_time FROM "
        "timepoint_observations ORDER BY stop_sequence").fetchall()


def test_clock_timed_journey_is_withheld_then_corrected_including_stored_readings():
    trips, audit = ClockChangeTrips(), audit_db.connect()
    match = _journey("26:05:00", "26:30:00")
    est, reason = _poll(match, (2026, 10, 24), 51.4500,
                        _utc(2026, 10, 25, 2, 6), trips, audit)
    assert est is None and reason == "clock_change_pending"
    assert _stored(audit) == [(0, 3660, 0)]   # standard reading, for now
    est, reason = _poll(match, (2026, 10, 24), 51.4700,
                        _utc(2026, 10, 25, 2, 31), trips, audit)
    assert reason is None and est.delay_s == 60 and est.event_type == "punctual"
    assert _stored(audit) == [(0, 60, 1), (10, 60, 1)]
    # Correcting again (for example after a restart) changes nothing.
    assert audit_db.apply_clock_reading(
        audit.cursor(), "20261024", "TRIP", match.schedule, LDN) == 0


def test_running_time_journey_keeps_the_standard_reading():
    trips, audit = ClockChangeTrips(), audit_db.connect()
    match = _journey("27:42:00", "28:23:00")      # route 4, FBRI-39411
    est, reason = _poll(match, (2026, 10, 24), 51.4500,
                        _utc(2026, 10, 25, 2, 42, 30), trips, audit)
    assert est is None and reason == "clock_change_pending"
    est, reason = _poll(match, (2026, 10, 24), 51.4700,
                        _utc(2026, 10, 25, 3, 23, 1), trips, audit)
    assert est.delay_s == 1
    assert _stored(audit) == [(0, 30, 1), (10, 1, 1)]


def test_clock_timed_journey_through_the_spring_change():
    trips, audit = ClockChangeTrips(), audit_db.connect()
    match = _journey("26:30:00", "26:50:00")      # 02:30 / 02:50 BST by the clock
    est, reason = _poll(match, (2026, 3, 28), 51.4500,
                        _utc(2026, 3, 29, 1, 31), trips, audit)
    assert est is None and reason == "clock_change_pending"
    assert _stored(audit) == []                   # -59 min on the standard
    est, reason = _poll(match, (2026, 3, 28), 51.4700,
                        _utc(2026, 3, 29, 1, 51), trips, audit)
    assert est.delay_s == 60
    assert _stored(audit) == [(10, 60, 1)]


def test_ordinary_day_is_untouched():
    trips, audit = ClockChangeTrips(), audit_db.connect()
    match = _journey("14:00:00", "14:20:00")
    est, reason = _poll(match, (2026, 10, 23), 51.4500,
                        _utc(2026, 10, 23, 13, 1), trips, audit)
    assert reason is None and est.delay_s == 60
    assert trips.decision("20261023", "TRIP") == "standard"
