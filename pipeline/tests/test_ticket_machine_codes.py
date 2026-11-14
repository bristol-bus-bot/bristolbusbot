"""#132: First trips take their ticket machine journey code from First's TXC."""
from __future__ import annotations

from datetime import date
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ticket_machine_codes  # noqa: E402
from timetable_calendar_evidence import Evidence  # noqa: E402
from timetable_source_index import SourceIndex, SourceJourney  # noqa: E402

CALLS = (("A", "08:00:00", "08:00:00"), ("B", "08:10:00", "08:10:00"))
OTHER = (("A", "09:00:00", "09:00:00"), ("B", "09:10:00", "09:10:00"))


def journey(code, calls, ticket_code, start=date(2026, 11, 8)):
    return SourceJourney("75", 0, code, calls,
                         Evidence(("PB0001", "75"), start, None, b"", "a", "f", "x.xml"),
                         ticket_code)


def database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
            CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
            CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
                                direction_id INTEGER, vehicle_journey_code TEXT);
            CREATE TABLE stop_times (trip_id TEXT, arrival_time TEXT, departure_time TEXT,
                                     stop_id TEXT, stop_sequence INTEGER);
            INSERT INTO agency VALUES ('OP1', 'FBRI'), ('OP2', 'ABUS');
            INSERT INTO routes VALUES ('R75', 'OP1', '75'), ('RX', 'OP2', '75');
        """)
        rows = [("coded", "R75", "VJ1", CALLS), ("missing", "R75", "VJ9", CALLS),
                ("blank", "R75", "VJ2", OTHER), ("clash", "R75", "VJ3", CALLS),
                ("other-operator", "RX", "VJ1", CALLS)]
        for trip, route, code, calls in rows:
            connection.execute("INSERT INTO trips VALUES (?,?,?,?,?)", (trip, route, "S", 0, code))
            for sequence, (stop, arrival, departure) in enumerate(calls):
                connection.execute("INSERT INTO stop_times VALUES (?,?,?,?,?)",
                                   (trip, arrival, departure, stop, sequence))


def test_codes_attach_only_when_first_source_journeys_agree(tmp_path):
    path = tmp_path / "timetable.db"
    database(path)
    index = SourceIndex()
    index.add(journey("VJ1", CALLS, "1020"))
    index.add(journey("VJ1", CALLS, "1020", start=date(2026, 11, 22)))   # same code, later edition
    index.add(journey("VJ2", OTHER, None))                                # source has no code
    index.add(journey("VJ3", CALLS, "1030"))
    index.add(journey("VJ3", CALLS, "1031", start=date(2026, 11, 22)))   # editions disagree

    counts = ticket_machine_codes.attach(path, index)

    assert counts == {"first_trips": 4, "coded": 1, "no_source_journey": 1,
                      "source_has_no_code": 1, "codes_disagree": 1}
    with sqlite3.connect(path) as connection:
        codes = dict(connection.execute(
            "SELECT trip_id, ticket_machine_journey_code FROM trips"))
    assert codes == {"coded": "1020", "missing": None, "blank": None, "clash": None,
                     "other-operator": None}
    # Running it again changes nothing.
    assert ticket_machine_codes.attach(path, index)["coded"] == 1
