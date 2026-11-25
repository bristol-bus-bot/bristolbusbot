"""Planning #155: judge a timing-point reading by what the timetable says.

At a stop where the timetable gives an earlier arrival than departure, a
reading during the wait is on time and only a reading before the arrival time
can be early. At a set-down-only stop a reading is never early; late is still
late. Rows below are copied from the 20 Nov 2026 timetable; the reading times
are the real ones from the 22 and 23 Nov readings listed on the ticket.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from collector.delay import settled_reading_result, timetable_window_delay
from collector.timeparse import gtfs_seconds, service_midnight

LDN = ZoneInfo("Europe/London")


def midnight(day: int, first_departure: str) -> datetime:
    hours, minutes, _ = map(int, first_departure.split(":"))
    return service_midnight(
        datetime(2026, 11, day, hours, minutes, tzinfo=LDN),
        gtfs_seconds(first_departure))


def at(day, h, m, s=0):
    # November: UK time is UTC.
    return datetime(2026, 11, day, h, m, s, tzinfo=timezone.utc)


def reading(schedule, stop_index, recorded, sm):
    row = schedule[stop_index]
    result, reason = settled_reading_result(
        row[4], row[5], recorded, schedule, sm)
    assert reason is None, reason
    return result


# First 3, 22 Nov: Dorchester Street, Bath, arrive 14:45, leave 14:52.
ROUTE_3 = [
    (0, "14:12:00", 1, "bthmgjt", 51.39946, -2.30044, "Bathford School", "14:12:00", 0),
    (19, "14:52:00", 1, "bthmtwg", 51.37797, -2.35942, "Dorchester Street", "14:45:00", 0),
    (37, "15:14:00", 1, "bthdptj", 51.40138, -2.39341, "Haviland Park", "15:14:00", 0),
]
SM_3 = midnight(22, "14:12:00")


def test_reading_during_timetabled_wait_is_on_time():
    result = reading(ROUTE_3, 1, at(22, 14, 48), SM_3)
    assert result.observed_delay_s == 0
    assert result.on_time


def test_reading_before_arrival_is_early_against_the_arrival_time():
    # The real reading: 14:40:59, 4 min 1 s before the 14:45 arrival. It was
    # counted 11 minutes early against 14:52; it is still early, by less.
    result = reading(ROUTE_3, 1, at(22, 14, 40, 59), SM_3)
    assert result.observed_delay_s == -241
    assert not result.on_time


def test_lateness_is_still_measured_from_the_departure_time():
    result = reading(ROUTE_3, 1, at(22, 15, 0), SM_3)
    assert result.observed_delay_s == 8 * 60


def test_reading_exactly_at_arrival_is_on_time():
    result = reading(ROUTE_3, 1, at(22, 14, 45), SM_3)
    assert result.observed_delay_s == 0


# Stagecoach South West FAL, 22 Nov: Bristol Airport, arrive 23:52, leave 00:05.
FAL = [
    (0, "23:30:00", 1, "bstptgm", 51.45924, -2.58837, "Cabot Circus", "23:30:00", 0),
    (9, "24:05:00", 1, "wsmpjam", 51.38737, -2.70953, "Bristol Airport Interchange", "23:52:00", 0),
    (41, "26:58:00", 1, "plytadg", 50.37287, -4.14355, "Plymouth Coach Station", "26:58:00", 1),
]


def test_fal_at_the_airport_wait_is_on_time_across_midnight():
    # Counted 13.6 minutes early before; 33 s before the arrival is on time.
    result = reading(FAL, 1, at(22, 23, 51, 27), midnight(22, "23:30:00"))
    assert result.observed_delay_s == -33
    assert result.on_time


# National Express 202, 22 Nov: Bristol Bus Station, arrive 05:55, leave 06:05.
NX_202 = [
    (0, "03:30:00", 1, "swatmap", 51.617196041, -3.946175842, "Swansea Bus Station Stand B", "03:30:00", 0),
    (4, "06:05:00", 1, "bstgjgm", 51.45895, -2.59334, "Bristol Bus Station", "05:55:00", 0),
    (7, "08:30:00", 1, "", 51.45913, -0.44648, "Heathrow Terminal 4", "08:30:00", 1),
]


def test_coach_arriving_before_its_arrival_time_is_still_early():
    result = reading(NX_202, 1, at(22, 5, 52, 10), midnight(22, "03:30:00"))
    assert result.observed_delay_s == -170


# National Express 040, 22 Nov, coming in from London: set-down only.
NX_040 = [
    (0, "17:00:00", 1, "", 51.49267, -0.14922, "Victoria Coach Station", "17:00:00", 0),
    (2, "19:55:00", 1, "bstgjgm", 51.45895, -2.59334, "Bristol Bus Station", "19:50:00", 1),
    (6, "20:10:00", 1, "bstdapm", 51.4789, -2.61673, "White Tree Roundabout", "20:10:00", 1),
]
SM_040 = midnight(22, "17:00:00")


def test_set_down_only_stop_is_never_early():
    # The real reading: 19:40:46, counted 14 minutes early before.
    result = reading(NX_040, 1, at(22, 19, 40, 46), SM_040)
    assert result.observed_delay_s == 0
    assert result.on_time


def test_set_down_only_stop_is_still_late():
    result = reading(NX_040, 1, at(22, 20, 10), SM_040)
    assert result.observed_delay_s == 15 * 60
    assert not result.on_time


# Counter-example, First 24, 23 Nov: no timetabled wait, pick-up allowed.
# FBRI-30042 really ran early stop after stop; it must stay early.
ROUTE_24 = [
    (0, "16:25:00", 1, "bstpgwp", 51.49682, -2.59203, "Southmead Hospital", "16:25:00", 0),
    (29, "17:32:00", 1, "bstgagp", 51.45216, -2.59684, "The Centre", "17:32:00", 0),
    (45, "18:00:00", 1, "bstapdp", 51.42986, -2.62827, "Langley Crescent", "18:00:00", 0),
]


def test_genuine_early_running_stays_early():
    result = reading(ROUTE_24, 1, at(23, 17, 20, 12), midnight(23, "16:25:00"))
    assert result.observed_delay_s == -708
    assert not result.on_time


def test_old_six_field_rows_keep_the_departure_measure():
    row = (19, "14:52:00", 1, "bthmtwg", 51.37797, -2.35942)
    assert timetable_window_delay(at(22, 14, 40, 59), row, SM_3, -659) == -659


def test_a_wait_can_bring_a_reading_inside_the_early_cut_off():
    # 20 minutes before departure is beyond the 15-minute cut-off on the
    # departure measure, but only 7 minutes before the 23:52 arrival.
    result = reading(FAL, 1, at(22, 23, 45), midnight(22, "23:30:00"))
    assert result.observed_delay_s == -7 * 60
