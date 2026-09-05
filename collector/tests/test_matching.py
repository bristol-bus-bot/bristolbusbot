from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from collector.matching import explain_match, match_exact, match_fuzzy, match_vehicle
from fixture_gtfs import build

LDN = ZoneInfo("Europe/London")
WED_1115 = datetime(2026, 6, 10, 11, 15, tzinfo=LDN)  # Wednesday


def cur():
    return build().cursor()


@pytest.mark.parametrize("origin,destination", [("S1", "S3"), ("0100A", "0100C")])
@pytest.mark.parametrize("exact", [False, True])
def test_reversed_endpoints_cannot_win_direction_fallback(origin, destination, exact):
    # Like the route 7 receipt: the reported direction has no departure in
    # range, while the reverse journey does. Time alone must not select it.
    connection = build()
    connection.execute("UPDATE trips SET vehicle_journey_code='RETURN' WHERE trip_id='T_IN'")
    match = match_vehicle(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115.replace(minute=26),
        "RETURN", enable_exact=exact, origin_ref=origin, destination_ref=destination)
    assert match is None


@pytest.mark.parametrize("origin,destination", [
    (None, None), ("S1", None), ("unknown", "S3"), ("S2", "S3"),
    ("S3", "S1"),
])
def test_endpoint_guard_preserves_unproven_and_correct_fallbacks(origin, destination):
    match = match_vehicle(
        cur(), "FBRI", "75", "outbound", WED_1115.replace(minute=26),
        origin_ref=origin, destination_ref=destination)
    assert match and match.trip_id == "T_IN"


def test_endpoint_guard_does_not_reject_circular_trip():
    connection = build()
    connection.execute("UPDATE stop_times SET stop_id='S3' WHERE trip_id='T_IN'")
    match = match_vehicle(
        connection.cursor(), "FBRI", "75", "inbound", WED_1115,
        origin_ref="S3", destination_ref="0100C")
    assert match and match.trip_id == "T_IN"


def test_fuzzy_matches_right_operator():
    m = match_fuzzy(cur(), "FBRI", "75", "outbound", WED_1115)
    assert m and m.trip_id == "T_OUT" and m.tier == "fuzzy"
    # Same route number, same time, other operator -> the OTHER trip
    m2 = match_fuzzy(cur(), "ABUS", "75", "outbound", WED_1115)
    assert m2 and m2.trip_id == "T_ABUS"


def test_fuzzy_time_window():
    # 9 minutes off: inside ±10 window
    m = match_fuzzy(cur(), "FBRI", "75", "outbound", WED_1115.replace(minute=24))
    assert m and m.trip_id == "T_OUT"
    # 11 minutes off from T_OUT: outside its window — but the no-direction
    # fallback legitimately catches the inbound 11:20 trip (audit behaviour:
    # direction is a preference, not a hard constraint)
    m11 = match_fuzzy(cur(), "FBRI", "75", "outbound", WED_1115.replace(minute=26))
    assert m11 and m11.trip_id == "T_IN"
    # A time with nothing in ±10 min at all: genuinely no match
    assert match_fuzzy(cur(), "FBRI", "75", "outbound", WED_1115.replace(minute=50)) is None


def test_fuzzy_direction_scoping_and_fallback():
    m = match_fuzzy(cur(), "FBRI", "75", "inbound", datetime(2026, 6, 10, 11, 20, tzinfo=LDN))
    assert m and m.trip_id == "T_IN"
    # Unknown direction string -> falls back to no-direction pass, still matches
    m2 = match_fuzzy(cur(), "FBRI", "75", "clockwise", WED_1115)
    assert m2 is not None


def test_fuzzy_weekend_excluded():
    sat = datetime(2026, 6, 13, 11, 15, tzinfo=LDN)
    assert match_fuzzy(cur(), "FBRI", "75", "outbound", sat) is None


def test_fuzzy_applies_calendar_date_removals_and_additions():
    connection = build()
    connection.execute(
        "INSERT INTO calendar_dates VALUES ('WK', '20260610', 2)")
    assert match_fuzzy(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115) is None

    connection.execute(
        "INSERT INTO calendar VALUES "
        "('ADDED',0,0,0,0,0,0,0,'20260601','20260630')")
    connection.execute(
        "INSERT INTO calendar_dates VALUES ('ADDED', '20260610', 1)")
    connection.execute(
        "INSERT INTO trips VALUES "
        "('T_ADDED','R75F','ADDED','Hengrove','',0,'','',0,NULL)")
    connection.execute(
        "INSERT INTO stop_times VALUES "
        "('T_ADDED','11:15:00','11:15:00','S1',0,'',0,0,NULL,1)")
    connection.commit()
    match = match_fuzzy(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115)
    assert match and match.trip_id == "T_ADDED"


def test_night_trip_previous_service_day():
    # 01:30 Thursday wall clock = Wednesday service day's 25:30 trip
    thu_0130 = datetime(2026, 6, 11, 1, 30, tzinfo=LDN)
    m = match_fuzzy(cur(), "FBRI", "N75", "outbound", thu_0130)
    assert m and m.trip_id == "T_NIGHT"


def test_exact_tier_gated_and_refuses_hhmm():
    c = cur()
    assert match_exact(c, "FBRI", "VJ_75_1115").trip_id == "T_OUT"
    assert match_exact(c, "FBRI", "1115") is None          # HHMM-shaped: refused
    assert match_exact(c, "ABUS", "VJ_75_1115") is None    # wrong operator
    # match_vehicle honours the flag
    m_off = match_vehicle(c, "FBRI", "75", "outbound", WED_1115, "VJ_75_1115",
                          enable_exact=False)
    assert m_off.tier == "fuzzy"
    m_on = match_vehicle(c, "FBRI", "75", "outbound", WED_1115, "VJ_75_1115",
                         enable_exact=True)
    assert m_on.tier == "exact"


def test_exact_tier_chooses_the_active_timetable_edition():
    connection = build()
    connection.execute(
        "UPDATE calendar SET end_date='20260609' WHERE service_id='WK'")
    connection.execute(
        "INSERT INTO calendar VALUES "
        "('NEW',1,1,1,1,1,0,0,'20260610','20270610')")
    connection.execute(
        "INSERT INTO trips VALUES "
        "('T_NEW','R75F','NEW','Hengrove','',0,'','',0,'VJ_75_1115')")
    connection.execute(
        "INSERT INTO stop_times VALUES "
        "('T_NEW','11:16:00','11:16:00','S1',0,'',0,0,NULL,1)")
    connection.commit()
    match = match_vehicle(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115,
        "VJ_75_1115", enable_exact=True)
    assert match and match.trip_id == "T_NEW"


def test_drop_dont_guess():
    assert match_fuzzy(cur(), "FBRI", "99", "outbound", WED_1115) is None
    assert match_fuzzy(cur(), "", "75", "outbound", WED_1115) is None


def test_fuzzy_anchors_on_each_trips_minimum_stop_sequence():
    connection = build()
    connection.execute(
        "INSERT INTO routes VALUES ('RLONG','OP1','LONG','Long first hop',3)")
    connection.execute(
        "INSERT INTO trips VALUES "
        "('T_LONG','RLONG','WK','End','',0,'','',0,NULL)")
    # The 15-minute first hop would put the old hard-coded sequence-1 anchor
    # outside the fuzzy window even though the origin time is an exact match.
    connection.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("T_LONG", "11:15:00", "11:15:00", "S1", 0, "", 0, 0, None, 1),
        ("T_LONG", "11:30:00", "11:30:00", "S2", 1, "", 0, 0, None, 1),
    ])
    connection.commit()

    match = match_fuzzy(
        connection.cursor(), "FBRI", "LONG", "outbound", WED_1115)
    assert match and match.trip_id == "T_LONG"


def test_fuzzy_still_supports_a_one_based_trip():
    connection = build()
    connection.execute(
        "INSERT INTO routes VALUES ('RONE','OP1','ONE','One based',3)")
    connection.execute(
        "INSERT INTO trips VALUES "
        "('T_ONE','RONE','WK','End','',0,'','',0,NULL)")
    connection.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("T_ONE", "11:15:00", "11:15:00", "S1", 1, "", 0, 0, None, 1),
        ("T_ONE", "11:20:00", "11:20:00", "S2", 2, "", 0, 0, None, 1),
    ])
    connection.commit()

    match = match_fuzzy(
        connection.cursor(), "FBRI", "ONE", "outbound", WED_1115)
    assert match and match.trip_id == "T_ONE"


# ── two-towns matcher regression (2026-07-03): one NOC, one line number ──

BRISTOL_POS = (51.4580, -2.5610)   # near S1-S3
JUNE_TUE_1606 = datetime(2026, 6, 16, 16, 6, tzinfo=LDN)


def test_same_noc_two_towns_position_picks_the_right_41():
    """FARTOWN 16:05 is temporally closer to a 16:06 origin than CITY 16:08;
    only the route-proximity gate can choose correctly."""
    m = match_fuzzy(cur(), "FBRI", "41", "inbound", JUNE_TUE_1606,
                    vehicle_pos=BRISTOL_POS)
    assert m is not None
    assert m.trip_id == "T_41_CITY"


def test_without_position_nearest_time_gap_wins_deterministically():
    """Without a position, the nearest time gap wins deterministically."""
    m = match_fuzzy(cur(), "FBRI", "41", "inbound", JUNE_TUE_1606)
    assert m is not None
    assert m.trip_id == "T_41_FAR"


def test_no_candidate_near_vehicle_drops_not_guesses():
    """A vehicle far from every candidate is left unmatched."""
    m = match_fuzzy(cur(), "FBRI", "41", "inbound", JUNE_TUE_1606,
                    vehicle_pos=(52.5, -1.9))  # Birmingham-ish
    assert m is None


def test_match_vehicle_passes_position_through():
    m = match_vehicle(cur(), "FBRI", "41", "inbound", JUNE_TUE_1606,
                      vehicle_pos=BRISTOL_POS)
    assert m is not None and m.trip_id == "T_41_CITY"


def test_explain_match_records_chosen_timetable_edition():
    connection = build()
    connection.execute("""
        CREATE TABLE route_service_editions (
            route_id TEXT, edition_start TEXT, effective_end TEXT)
    """)
    connection.execute(
        "INSERT INTO route_service_editions VALUES "
        "('R75F','20260601','20260630')")
    match = match_fuzzy(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115,
        vehicle_pos=(51.4500, -2.5890))

    explanation = explain_match(
        connection.cursor(), match, "FBRI", "75", "outbound", WED_1115,
        (51.4500, -2.5890), "20260610")

    assert explanation["chosen"]["trip_id"] == "T_OUT"
    assert explanation["chosen"]["route_id"] == "R75F"
    assert explanation["chosen"]["service_id"] == "WK"
    assert explanation["chosen"]["timetable_edition"] == "20260601"
    assert explanation["candidate_count"] == 1


def test_explain_match_bounds_a_hostile_candidate_set():
    connection = build()
    match = match_fuzzy(
        connection.cursor(), "FBRI", "75", "outbound", WED_1115,
        vehicle_pos=(51.4500, -2.5890))
    connection.executemany(
        "INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (f"T_NOISE_{i:03d}", "R75F", "WK", "Hengrove", "", 0,
             f"NOISE_{i}", "", 0, None)
            for i in range(100)
        ])
    connection.executemany(
        "INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (f"T_NOISE_{i:03d}", "11:15:00", "11:15:00", "S1", 0,
             "", 0, 0, None, 1)
            for i in range(100)
        ])

    explanation = explain_match(
        connection.cursor(), match, "FBRI", "75", "outbound", WED_1115,
        (51.4500, -2.5890), "20260610")

    assert explanation["candidates_truncated"] is True
    assert explanation["candidate_count"] <= 51
    assert len(explanation["alternatives"]) == 3


def test_diagnostics_keep_distinct_overlapping_trip_editions():
    connection = build()
    connection.executescript("""
        CREATE TABLE route_service_editions (
            route_id TEXT,edition_start TEXT,effective_end TEXT);
        INSERT INTO route_service_editions VALUES
            ('R75F','20260601','20260630'),
            ('R75F','20260608','20260731');
        INSERT INTO calendar
            SELECT 'NEWER',monday,tuesday,wednesday,thursday,friday,
                saturday,sunday,'20260608','20260731'
            FROM calendar WHERE service_id='WK';
        INSERT INTO trips
            SELECT 'T_NEW',route_id,'NEWER',trip_headsign,trip_short_name,
                direction_id,block_id,shape_id,wheelchair_accessible,
                vehicle_journey_code FROM trips WHERE trip_id='T_OUT';
        INSERT INTO stop_times
            SELECT 'T_NEW',arrival_time,departure_time,stop_id,stop_sequence,
                stop_headsign,pickup_type,drop_off_type,shape_dist_traveled,
                timepoint FROM stop_times WHERE trip_id='T_OUT';
    """)
    match = match_fuzzy(connection.cursor(), "FBRI", "75", "outbound",
                        WED_1115, vehicle_pos=(51.4500, -2.5890))
    details = explain_match(connection.cursor(), match, "FBRI", "75",
                            "outbound", WED_1115, (51.4500, -2.5890), "20260610")
    entries = [details["chosen"], *details["alternatives"]]
    assert {row["trip_id"]: row["timetable_edition"] for row in entries} == {
        "T_OUT": "20260601", "T_NEW": "20260608"}
