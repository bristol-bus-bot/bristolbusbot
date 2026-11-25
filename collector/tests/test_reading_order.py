"""Planning #156: keep only readings that fit the bus's progress.

Each example replays, in time order, the readings the collector stored for a
real journey on 22 or 23 Nov 2026 (as listed on the ticket). Before the fix
the out-of-order reading was stored or replaced a good one.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from collector import audit_db, live_db
from collector.matching import match_fuzzy
from collector.run import FollowedTrips, vm_cycle
from fixture_gtfs import build
from test_run import NOW, VM_FEED, setup

LDN = ZoneInfo("Europe/London")


def obs(trip, seq, recorded, delay_s, distance_m=10, *, final=False,
        origin=False, vehicle="FBRI-1", day="20261122"):
    return (day, "FBRI", "R", trip, "ref", seq, f"S{seq}",
            "2026-11-22T00:00:00+00:00", delay_s, int(-60 <= delay_s <= 359),
            distance_m, recorded, vehicle, int(origin), "fuzzy", int(final))


def store(readings, day="20261122"):
    conn = audit_db.connect()
    for reading in readings:
        audit_db.upsert_observation(conn.cursor(), reading)
    return {seq: (recorded, delay, distance) for seq, recorded, delay, distance
            in conn.execute(
                "SELECT stop_sequence, recorded_at, observed_delay_s, "
                "gps_distance_m FROM timepoint_observations "
                "WHERE service_date=? ORDER BY stop_sequence", (day,))}


def test_a1_layover_reading_cannot_replace_the_arrival():
    # First A1, FBRI-30164, 22 Nov: on time to stop 15, reached Bristol Bus
    # Station; at 08:24, 12 m away and still on the old journey code, it was
    # kept as the last-stop reading, 63 minutes late.
    kept = store([
        obs("A1", 12, "07:08", 0),
        obs("A1", 15, "07:11", 60),
        obs("A1", 21, "07:23", 60, 30, final=True),
        obs("A1", 21, "08:24", 3780, 12, final=True),
    ])
    assert kept[21] == ("07:23", 60, 30)


def test_70_return_pass_after_the_journey_ended_is_not_stored():
    # First 70, FBRI-39455, 22 Nov, 18:00 journey: stop 16 at 19:31 (+70).
    kept = store([
        obs("70", 11, "18:20", 360),
        obs("70", 26, "18:47", 480),
        obs("70", 29, "18:58", 660, 12, final=True),
        obs("70", 16, "19:31", 4200, 22),
    ])
    assert 16 not in kept
    assert set(kept) == {11, 26, 29}


def test_route_1_return_journey_under_the_old_code_is_not_stored():
    # First 1, FBRI-39415, 23 Nov: stops 29, 23, 17, 11, 0 recorded backwards.
    kept = store([
        obs("1", 29, "13:40", -780, day="20261123"),
        obs("1", 23, "13:51", 840, day="20261123"),
        obs("1", 17, "13:59", 1920, day="20261123"),
        obs("1", 11, "14:08", 3000, day="20261123"),
        obs("1", 0, "14:22", 4860, origin=True, day="20261123"),
    ], day="20261123")
    assert set(kept) == {29}


def test_route_8_second_pass_of_the_loop_is_not_stored():
    # First 8, FBRI-65139, 22 Nov: stop 21 at 07:58, then stop 7 at 08:10.
    kept = store([
        obs("8", 0, "07:28", -60, origin=True),
        obs("8", 21, "07:58", 420),
        obs("8", 7, "08:10", 1920, 31),
        obs("8", 31, "08:11", 420),
    ])
    assert 7 not in kept
    assert set(kept) == {0, 21, 31}


def test_an_earlier_stop_cannot_be_replaced_once_a_later_one_is_stored():
    kept = store([
        obs("X", 5, "10:00", 60, 80),
        obs("X", 9, "10:10", 60, 20),
        obs("X", 5, "10:30", 1860, 5),   # closer, but the bus has moved on
    ])
    assert kept[5] == ("10:00", 60, 80)


def test_closer_reading_still_replaces_while_the_bus_is_at_that_stop():
    kept = store([
        obs("X", 5, "10:00", 60, 80),
        obs("X", 5, "10:00:30", 90, 15),
    ])
    assert kept[5] == ("10:00:30", 90, 15)


def test_last_stop_far_reading_can_still_be_improved():
    # Approaching the end: 400 m away is not "passed the last stop" yet.
    kept = store([
        obs("X", 5, "10:00", 60),
        obs("X", 9, "10:10", 60, 400, final=True),
        obs("X", 9, "10:11", 120, 20, final=True),
    ])
    assert kept[9] == ("10:11", 120, 20)


def test_genuinely_very_late_bus_stays_late():
    # Counter-example: First D1x, FBRI-67097, 23 Nov, 14:34 journey, about
    # 89 minutes late at every stop, in order. All of it must be kept.
    kept = store([
        obs("D1x", 22, "16:33", 5340, 1, day="20261123"),
        obs("D1x", 26, "16:37", 5400, 106, day="20261123"),
        obs("D1x", 35, "16:55", 5280, 5, final=True, day="20261123"),
    ], day="20261123")
    assert {seq: delay for seq, (_, delay, _) in kept.items()} == {
        22: 5340, 26: 5400, 35: 5280}


def test_other_journeys_are_not_affected():
    kept = store([
        obs("A", 29, "10:00", 60, final=True),
        obs("B", 3, "10:05", 60),
    ])
    assert set(kept) == {3, 29}


# A second bus on a journey (Eurocoaches Y8, 23 Nov: one bus ran the 18:06
# on time and a second, sending a different journey code, was matched too).

def test_fuzzy_match_skips_a_journey_another_bus_is_following():
    tt = build().cursor()
    origin = datetime(2026, 6, 10, 11, 15, tzinfo=LDN)
    assert match_fuzzy(tt, "FBRI", "75", "outbound", origin).trip_id == "T_OUT"
    assert match_fuzzy(tt, "FBRI", "75", "outbound", origin,
                       taken_trips={"T_OUT"}) is None


def test_followed_trips_ignore_the_bus_itself_and_release_on_change():
    live = live_db.connect()
    followed = FollowedTrips(live, datetime(2026, 6, 10, tzinfo=timezone.utc))
    followed.follow("BUS-1", "T_OUT")
    assert "T_OUT" in followed.taken_by_others("BUS-2")
    assert "T_OUT" not in followed.taken_by_others("BUS-1")
    followed.follow("BUS-1", "T_IN")
    assert "T_OUT" not in followed.taken_by_others("BUS-2")
    assert "T_IN" in followed.taken_by_others("BUS-2")


def two_buses(first="FBRI-36205", second="FBRI-99999"):
    activity = VM_FEED.split("<VehicleActivity>")[1].split("</VehicleActivity>")[0]
    other = activity.replace(f"<VehicleRef>{first}</VehicleRef>",
                             f"<VehicleRef>{second}</VehicleRef>")
    return VM_FEED.replace(
        "</VehicleActivity>", f"</VehicleActivity><VehicleActivity>{other}"
        "</VehicleActivity>", 1)


def test_second_bus_in_the_same_poll_is_not_matched():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    result = vm_cycle(lambda: two_buses(), tt, live_conn, audit_conn,
                      boundary, cfg, LDN, now_utc=NOW)
    assert result["candidates"] == 2 and result["matched"] == 1
    trips = dict(live_conn.execute("SELECT vehicle_ref, trip_id FROM vehicles"))
    assert trips == {"FBRI-36205": "T_OUT", "FBRI-99999": None}


def test_bus_already_following_keeps_its_journey_on_the_next_poll():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg, LDN,
             now_utc=NOW)
    feed = two_buses().replace(
        "<VehicleRef>FBRI-36205</VehicleRef>", "<VehicleRef>TMP</VehicleRef>", 1
    ).replace("<VehicleRef>FBRI-99999</VehicleRef>",
              "<VehicleRef>FBRI-36205</VehicleRef>").replace(
        "<VehicleRef>TMP</VehicleRef>", "<VehicleRef>FBRI-99999</VehicleRef>")
    # Now the newcomer comes first in the feed; the bus already on the
    # journey still keeps it.
    result = vm_cycle(lambda: feed, tt, live_conn, audit_conn, boundary, cfg,
                      LDN, now_utc=NOW)
    assert result["matched"] == 1
    trips = dict(live_conn.execute("SELECT vehicle_ref, trip_id FROM vehicles"))
    assert trips == {"FBRI-36205": "T_OUT", "FBRI-99999": None}


# A last-stop reading taken before the journey has started (Tom, 25 Nov):
# it must not block the journey's later readings. Rows from the 20 Nov
# timetable; reading times are the real ones from the ticket.

def nov(day, h, m, s=0):
    return datetime(2026, 11, day, h, m, s, tzinfo=timezone.utc)


def sm(day, first):
    from collector.timeparse import gtfs_seconds, service_midnight
    h, m, _ = map(int, first.split(":"))
    return service_midnight(datetime(2026, 11, day, h, m, tzinfo=LDN),
                            gtfs_seconds(first))


ROUTE_3_0611 = [
    (0, "06:11:00", 1, "bthmgaj", 51.40367, -2.31231, "Morris Lane"),
    (1, "06:12:00", 1, "bthmdpt", 51.4054, -2.31783, "Stambridge"),
    (5, "06:15:00", 1, "bthmatw", 51.40007, -2.33319, "Bailbrook House"),
    (14, "06:25:00", 1, "bthmtwg", 51.37797, -2.35942, "Dorchester Street"),
]


def settle(schedule, index, recorded, midnight):
    from collector.delay import settled_reading_result
    row = schedule[index]
    return settled_reading_result(row[4], row[5], recorded, schedule, midnight)


def test_route_3_last_stop_before_the_start_does_not_block_the_journey():
    # First 3, FBRI-30218, 23 Nov, 06:11 journey: recorded at Dorchester
    # Street (its last stop) at 06:11:34, then left Morris Lane 16 late.
    midnight = sm(23, "06:11:00")
    reading, reason = settle(ROUTE_3_0611, 3, nov(23, 6, 11, 34), midnight)
    assert reading is None and reason == "last_stop_before_start"
    # So the journey's real readings are all kept.
    kept = store([
        obs("3", 0, "06:26", 960, origin=True, day="20261123"),
        obs("3", 1, "06:28", 1020, day="20261123"),
        obs("3", 5, "06:31", 1020, day="20261123"),
    ], day="20261123")
    assert set(kept) == {0, 1, 5}


def test_route_31_last_stop_before_the_start_is_not_stored():
    # First 31, FBRI-30209, 22 Nov, 11:15 journey: at Lansdown P&R at 11:10.
    route_31 = [
        (0, "11:15:00", 1, "bthjmpj", 51.38334, -2.36292, "Queen Square"),
        (12, "11:25:00", 1, "bthdwaj", 51.41124, -2.38719, "Lansdown P&R"),
    ]
    reading, reason = settle(route_31, 1, nov(22, 11, 10, 12),
                             sm(22, "11:15:00"))
    assert reading is None and reason == "last_stop_before_start"


def test_real_last_stop_arrival_is_still_stored():
    # The same route 3 journey arriving at 06:41 (16 late) is a real arrival.
    reading, reason = settle(ROUTE_3_0611, 3, nov(23, 6, 41), sm(23, "06:11:00"))
    assert reason is None and reading.is_final
    assert reading.observed_delay_s == 16 * 60


def test_early_but_possible_last_stop_arrival_is_still_stored():
    # 4 minutes early at the end of a 14-minute journey: past halfway, kept.
    reading, reason = settle(ROUTE_3_0611, 3, nov(23, 6, 21), sm(23, "06:11:00"))
    assert reason is None and reading.observed_delay_s == -240
