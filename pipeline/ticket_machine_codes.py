"""Carry First's ticket machine journey codes onto timetable trips (planning #132).

The live feed's ``DatedVehicleJourneyRef`` corresponds to TransXChange's
``Operational/TicketMachine/JourneyCode``, the pairing BODS's own matcher
uses. BODS's GTFS doesn't carry that field: its ``vehicle_journey_code`` is
TransXChange's ``VehicleJourneyCode`` (``VJ1673``). So each First GTFS trip is
paired with First's own TransXChange journeys on the exact key the calendar
reconciliation already uses (line, direction, vehicle journey code, full call
sequence), and takes the code only when every matching journey agrees.

Nothing here changes which trips run or how buses are matched; the column is
for the offline agreement check. No matching tier uses it yet.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3
from pathlib import Path


def ensure_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(trips)")}
    if "ticket_machine_journey_code" not in columns:
        connection.execute("ALTER TABLE trips ADD COLUMN ticket_machine_journey_code TEXT")


def attach(database: Path, index) -> dict:
    """Fill ``trips.ticket_machine_journey_code`` for First trips. Returns counts."""
    counts = {"first_trips": 0, "coded": 0, "no_source_journey": 0,
              "source_has_no_code": 0, "codes_disagree": 0}
    with sqlite3.connect(database) as connection:
        ensure_column(connection)
        trips = {row[0]: row[1:] for row in connection.execute(
            """SELECT t.trip_id, r.route_short_name, t.direction_id, t.vehicle_journey_code
                 FROM trips t JOIN routes r USING(route_id) JOIN agency a USING(agency_id)
                WHERE a.agency_noc = 'FBRI'""")}
        counts["first_trips"] = len(trips)
        if not trips:
            return counts
        calls = defaultdict(list)
        for trip, stop, arrival, departure in connection.execute(
                "SELECT trip_id, stop_id, arrival_time, departure_time FROM stop_times "
                "ORDER BY trip_id, stop_sequence"):
            if trip in trips:
                calls[trip].append((stop, arrival, departure))
        updates = []
        for trip, (line, direction, code) in trips.items():
            journeys = index.exact_journeys(
                (line, int(direction or 0), code, tuple(calls.get(trip, ()))))
            if not journeys:
                counts["no_source_journey"] += 1
                continue
            codes = {journey.ticket_machine_code for journey in journeys}
            if codes == {None}:
                counts["source_has_no_code"] += 1
                continue
            codes.discard(None)
            if len(codes) != 1:
                counts["codes_disagree"] += 1
                continue
            updates.append((codes.pop(), trip))
        connection.executemany(
            "UPDATE trips SET ticket_machine_journey_code = ? WHERE trip_id = ?", updates)
        counts["coded"] = len(updates)
    return counts
