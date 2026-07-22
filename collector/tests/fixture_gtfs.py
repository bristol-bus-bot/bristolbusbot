"""Builds a tiny in-memory GTFS SQLite database mirroring timetable.db's schema.

Contents designed to exercise the matcher's edge cases:
- FBRI route 75, weekday, outbound 11:15 + inbound 11:20 (direction scoping)
- ABUS also runs a route '75' (operator scoping — same number, different NOC)
- FBRI route N75 night trip departing '25:30:00' on the WEDNESDAY service
  (i.e. 01:30 Thursday wall-clock — the pre-06:00 previous-day window)
- vehicle_journey_code on the 11:15 trip (exact-tier testing)
- FBRI runs line '41' in TWO towns (a real 2026-07 matcher regression):
  CITY trips near the S1-S3 stops, and FARTOWN trips ~18 km east — same
  NOC, same line number, overlapping departure windows
  (candidate-selection testing)
"""
import sqlite3

SCHEMA = """
CREATE TABLE agency (agency_id TEXT PRIMARY KEY, agency_name TEXT, agency_url TEXT,
    agency_timezone TEXT, agency_lang TEXT, agency_phone TEXT, agency_noc TEXT);
CREATE TABLE routes (route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT,
    route_long_name TEXT, route_type INTEGER);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, monday INT, tuesday INT, wednesday INT,
    thursday INT, friday INT, saturday INT, sunday INT, start_date TEXT, end_date TEXT);
CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INT);
CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
    trip_headsign TEXT, trip_short_name TEXT, direction_id INT, block_id TEXT,
    shape_id TEXT, wheelchair_accessible INT, vehicle_journey_code TEXT);
CREATE TABLE stops (stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT,
    stop_lat REAL, stop_lon REAL, wheelchair_boarding INT, location_type INT,
    parent_station TEXT, platform_code TEXT);
CREATE TABLE stop_times (trip_id TEXT, arrival_time TEXT, departure_time TEXT,
    stop_id TEXT, stop_sequence INT, stop_headsign TEXT, pickup_type INT,
    drop_off_type INT, shape_dist_traveled REAL, timepoint INT);
"""


def build() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    c = conn.cursor()
    c.executemany("INSERT INTO agency VALUES (?,?,?,?,?,?,?)", [
        ("OP1", "First Bristol", "", "Europe/London", "", "", "FBRI"),
        ("OP2", "ABus", "", "Europe/London", "", "", "ABUS"),
    ])
    c.executemany("INSERT INTO routes VALUES (?,?,?,?,?)", [
        ("R75F", "OP1", "75", "Hengrove - City", 3),
        ("R75A", "OP2", "75", "Somewhere else", 3),
        ("RN75", "OP1", "N75", "Night bus", 3),
        ("R41C", "OP1", "41", "City 41", 3),
        ("R41F", "OP1", "41", "Fartown 41", 3),
    ])
    # weekdays only, valid across June 2026
    c.execute("INSERT INTO calendar VALUES ('WK',1,1,1,1,1,0,0,'20260601','20260630')")
    c.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("T_OUT", "R75F", "WK", "Hengrove", "", 0, "B1", "", 0, "VJ_75_1115"),
        ("T_IN",  "R75F", "WK", "City",     "", 1, "B1", "", 0, None),
        ("T_ABUS","R75A", "WK", "Elsewhere","", 0, "",   "", 0, None),
        ("T_NIGHT","RN75","WK", "Night",    "", 0, "",   "", 0, None),
        # the two-towns shape: same NOC+line+direction, different towns.
        # FARTOWN's 16:05 is temporally CLOSER to a 16:06 origin than
        # CITY's 16:08 — only geography can pick correctly.
        ("T_41_CITY", "R41C", "WK", "City",    "", 1, "", "", 0, None),
        ("T_41_FAR",  "R41F", "WK", "Fartown", "", 1, "", "", 0, None),
    ])
    c.executemany("INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?)", [
        ("S1", "0100A", "Origin",  51.4600, -2.5890, 0, 0, None, None),
        ("S2", "0100B", "Middle",  51.4550, -2.5890, 0, 0, None, None),
        ("S3", "0100C", "End",     51.4500, -2.5890, 0, 0, None, None),
        ("F1", "0200A", "Fartown Origin", 51.3800, -2.3600, 0, 0, None, None),
        ("F2", "0200B", "Fartown End",    51.3750, -2.3550, 0, 0, None, None),
    ])
    def st(trip, dep, stop, seq, tp):
        return (trip, dep, dep, stop, seq, "", 0, 0, None, tp)
    c.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?,?)", [
        st("T_OUT",  "11:15:00", "S1", 1, 1),
        st("T_OUT",  "11:20:00", "S2", 2, 0),
        st("T_OUT",  "11:25:00", "S3", 3, 1),
        st("T_IN",   "11:20:00", "S3", 1, 1),
        st("T_IN",   "11:30:00", "S1", 2, 1),
        st("T_ABUS", "11:15:00", "S1", 1, 1),   # same number+time, other operator
        st("T_NIGHT","25:30:00", "S1", 1, 1),   # 01:30 next wall-clock day
        st("T_NIGHT","25:40:00", "S3", 2, 1),
        st("T_41_CITY", "16:08:00", "S1", 1, 1),
        st("T_41_CITY", "16:18:00", "S3", 2, 1),
        st("T_41_FAR",  "16:05:00", "F1", 1, 1),
        st("T_41_FAR",  "16:15:00", "F2", 2, 1),
    ])
    conn.commit()
    return conn
