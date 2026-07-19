from datetime import datetime
from zoneinfo import ZoneInfo

from collector.matching import match_exact, match_fuzzy, match_vehicle
from fixture_gtfs import build

LDN = ZoneInfo("Europe/London")
WED_1115 = datetime(2026, 6, 10, 11, 15, tzinfo=LDN)  # Wednesday


def cur():
    return build().cursor()


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


def test_drop_dont_guess():
    assert match_fuzzy(cur(), "FBRI", "99", "outbound", WED_1115) is None
    assert match_fuzzy(cur(), "", "75", "outbound", WED_1115) is None


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
