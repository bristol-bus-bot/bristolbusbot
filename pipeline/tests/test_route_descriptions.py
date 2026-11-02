import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import route_descriptions  # noqa: E402
from audit_operators import NETWORK_LABEL, public_route  # noqa: E402

TOWNS = {"BUS": "Bristol", "CLIF": "Clifton", "FAIL": "Failand", "TICK": "Tickenham",
         "SIX": "Clevedon", "TRI": "Clevedon", "BATH": "Bath", "OLD": "Oldfield Park"}


def timetable(trips):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (trip_id TEXT, route_id TEXT, direction_id INTEGER);
        CREATE TABLE stops (stop_id TEXT, stop_code TEXT, stop_name TEXT);
        CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, stop_sequence INTEGER, timepoint INTEGER);
        INSERT INTO agency VALUES ('A', 'FBRI');
    """)
    names = {"BUS": "Bus Station", "CLIF": "Clifton Down", "FAIL": "Failand Cross Roads",
             "TICK": "Tickenham Church", "SIX": "Six Ways", "TRI": "Triangle",
             "BATH": "Bus Station", "OLD": "Moorland Road"}
    for code, name in names.items():
        conn.execute("INSERT INTO stops VALUES (?,?,?)", (code, code, name))
    for trip, route, direction, stops in trips:
        conn.execute("INSERT OR IGNORE INTO routes VALUES (?, 'A', ?)", (route, route))
        conn.execute("INSERT INTO trips VALUES (?,?,?)", (trip, route, direction))
        for seq, code in enumerate(stops):
            conn.execute("INSERT INTO stop_times VALUES (?,?,?,1)", (trip, code, seq))
    return conn


def test_a_route_between_towns_reads_from_to_via():
    tt = timetable([
        ("t1", "X6", 0, ["BUS", "CLIF", "FAIL", "TICK", "SIX"]),
        ("t2", "X6", 0, ["BUS", "CLIF", "FAIL", "TICK", "SIX"]),
        ("t3", "X6", 1, ["SIX", "TICK", "BUS"]),
    ])
    out = route_descriptions.build(tt, towns=TOWNS)
    assert out[("FBRI", "X6")] == "Bristol to Clevedon, via Clifton, Failand and Tickenham"


def test_a_loop_and_a_same_town_route_read_sensibly():
    assert route_descriptions.describe_pattern(
        [("BATH", "Bus Station"), ("OLD", "Moorland Road"), ("BATH", "Bus Station")], TOWNS
    ) == "Circular from Bus Station, Bath, via Oldfield Park"
    assert route_descriptions.describe_pattern(
        [("TRI", "Triangle"), ("SIX", "Six Ways")], TOWNS) == "Triangle to Six Ways, Clevedon"
    assert route_descriptions.describe_pattern([("TRI", "Triangle")], TOWNS) is None


def test_descriptions_attach_to_operator_and_network_rows():
    summary = {"period": {"by_operator": {
        "FBRI": {"routes": [{"route": "X6"}, {"route": "99"}]},
        NETWORK_LABEL: {"routes": [{"route": "X6"}, {"route": "20 Big Lemon"}]},
    }}, "latest_day": {}}
    added = route_descriptions.attach(
        summary, {("FBRI", "X6"): "Bristol to Clevedon", ("LEMB", "20"): "Twerton to Bath"},
        public_route, NETWORK_LABEL)
    rows = summary["period"]["by_operator"]
    assert rows["FBRI"]["routes"][0]["description"] == "Bristol to Clevedon"
    assert "description" not in rows["FBRI"]["routes"][1]
    assert rows[NETWORK_LABEL]["routes"][1]["description"] == "Twerton to Bath"
    assert added == 3


def test_no_timetable_tables_means_no_descriptions():
    assert route_descriptions.build(sqlite3.connect(":memory:"), towns=TOWNS) == {}


def test_one_unknown_end_names_both_stops_rather_than_guessing_a_town():
    assert route_descriptions.describe_pattern(
        [("BATH", "Royal United Hospital"), ("NOWHERE", "Somerset Arms")], {"BATH": "Combe Park"}
    ) == "Royal United Hospital (Combe Park) to Somerset Arms"


def test_same_named_ends_with_one_unknown_town_read_as_from():
    assert route_descriptions.describe_pattern(
        [("BUS", "Bus Station"), ("CLIF", "Clifton Down"), ("FARAWAY", "Bus Station")], TOWNS
    ) == "From Bus Station (Bristol), via Clifton"
