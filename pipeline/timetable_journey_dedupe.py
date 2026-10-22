"""List each public journey at most once per day, for every operator.

Some publishers send one journey under several calendars that overlap. In
October 2026 FlixBus listed each coach journey under up to nine "daily except"
calendars, so most days showed several copies of one coach. That inflated
expected trips and blocked three weeks of timetable updates.

This step needs no source evidence because it never changes what runs. A
journey is the same when operator, public route, direction, headsign and every
stop with its times are identical, matching the Pi's acceptance gate. Where two
copies are active on the same date, the later copy keeps its trip row, its
vehicle-journey code and every other date, but a private calendar clone removes
the dates an earlier copy already covers. The union of operating dates is
unchanged, and no trip or stop-time row is deleted.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import sqlite3
from pathlib import Path

from timetable_duplicate_evidence import active_days

CLONE_MARKER = "~onceperday~"


def _journey_identities(connection: sqlite3.Connection) -> dict[str, bytes]:
    """Hash each trip's public identity, streaming stop times in order.

    Python does the ordering rather than GROUP_CONCAT(... ORDER BY), which
    needs SQLite 3.44 and the build runner's version is not pinned.
    """
    details = {
        str(trip_id): "\x1f".join(str(value) for value in (
            operator, route, direction, headsign))
        for trip_id, operator, route, direction, headsign in connection.execute("""
            SELECT t.trip_id,
                   COALESCE(NULLIF(a.agency_noc, ''), a.agency_id, 'unknown'),
                   COALESCE(NULLIF(r.route_short_name, ''), r.route_id, 'unknown'),
                   COALESCE(t.direction_id, 0),
                   COALESCE(t.trip_headsign, '')
              FROM trips t
              JOIN routes r ON r.route_id = t.route_id
              JOIN agency a ON a.agency_id = r.agency_id""")
    }
    identities: dict[str, bytes] = {}
    current, digest = None, None

    def finish():
        if current is not None and current in details:
            identities[current] = digest.digest()

    for trip_id, sequence, stop_id, arrival, departure in connection.execute("""
            SELECT trip_id, stop_sequence, COALESCE(stop_id, ''),
                   COALESCE(arrival_time, ''), COALESCE(departure_time, '')
              FROM stop_times
             ORDER BY trip_id, stop_sequence, stop_id, arrival_time, departure_time"""):
        trip_id = str(trip_id)
        if trip_id != current:
            finish()
            current = trip_id
            digest = hashlib.blake2b(digest_size=16)
            digest.update(details.get(trip_id, "").encode("utf-8"))
        digest.update(f"\x1e{sequence}\x1f{stop_id}\x1f{arrival}\x1f{departure}".encode("utf-8"))
    finish()
    return identities


def _service_days(connection: sqlite3.Connection, service_ids: set[str]):
    connection.row_factory = sqlite3.Row
    calendars = {}
    exceptions: dict[str, list[tuple[str, int]]] = defaultdict(list)
    ids = sorted(service_ids)
    for index in range(0, len(ids), 500):
        chunk = ids[index:index + 500]
        marks = ",".join("?" * len(chunk))
        for row in connection.execute(
                f"SELECT * FROM calendar WHERE service_id IN ({marks})", chunk):
            calendars[str(row["service_id"])] = row
        for row in connection.execute(
                f"SELECT service_id, date, exception_type FROM calendar_dates "
                f"WHERE service_id IN ({marks})", chunk):
            exceptions[str(row[0])].append((str(row[1]), int(row[2])))
    connection.row_factory = None
    return calendars, exceptions


def collapse_same_day_copies(path: Path | str) -> dict[str, object]:
    connection = sqlite3.connect(path)
    try:
        identities = _journey_identities(connection)
        trips_by_identity: dict[bytes, list[tuple[str, str, str]]] = defaultdict(list)
        operator_of = {}
        for trip_id, service_id, operator in connection.execute("""
                SELECT t.trip_id, t.service_id,
                       COALESCE(NULLIF(a.agency_noc, ''), a.agency_id, 'unknown')
                  FROM trips t
                  JOIN routes r ON r.route_id = t.route_id
                  JOIN agency a ON a.agency_id = r.agency_id"""):
            identity = identities.get(str(trip_id))
            if identity is not None:
                trips_by_identity[identity].append((str(trip_id), str(service_id), str(operator)))
                operator_of[str(trip_id)] = str(operator)
        groups = [sorted(group) for group in trips_by_identity.values()
                  if len({service for _, service, _ in group}) > 1]
        services = {service for group in groups for _, service, _ in group}
        calendars, exceptions = _service_days(connection, services)
        days_of = {service: active_days(calendars.get(service), exceptions.get(service, []))
                   for service in services}

        clones: dict[str, tuple[str, frozenset]] = {}
        moves: list[tuple[str, str]] = []
        by_operator: dict[str, int] = defaultdict(int)
        removed_listings = 0
        for group in groups:
            # The copy with the earliest first date is kept whole, so the
            # longest-standing listing stays the one that matches by default.
            group.sort(key=lambda item: (
                min((day.toordinal() for day in days_of[item[1]]), default=0), item[0]))
            claimed: set = set()
            for trip_id, service_id, operator in group:
                days = days_of[service_id]
                overlap = frozenset(days & claimed)
                claimed |= days
                if not overlap:
                    continue
                key = hashlib.sha256((service_id + "|" + ",".join(
                    sorted(day.isoformat() for day in overlap))).encode()).hexdigest()[:16]
                clone_id = f"{service_id}{CLONE_MARKER}{key}"
                clones.setdefault(clone_id, (service_id, overlap))
                moves.append((clone_id, trip_id))
                by_operator[operator] += 1
                removed_listings += len(overlap)

        columns = [row[1] for row in connection.execute("PRAGMA table_info(calendar)")]
        with connection:
            for clone_id, (service_id, overlap) in sorted(clones.items()):
                removed = {day.strftime("%Y%m%d") for day in overlap}
                calendar = calendars.get(service_id)
                if calendar is not None:
                    values = [clone_id if name == "service_id" else calendar[name]
                              for name in columns]
                    connection.execute(
                        f"INSERT OR REPLACE INTO calendar ({','.join(columns)}) "
                        f"VALUES ({','.join('?' * len(columns))})", values)
                connection.execute("DELETE FROM calendar_dates WHERE service_id=?", (clone_id,))
                rows = [(clone_id, day, kind) for day, kind in exceptions.get(service_id, [])
                        if day not in removed]
                # Removal rows matter only where the clone would otherwise run.
                rows += [(clone_id, day, 2) for day in sorted(removed)
                         if calendar is not None]
                connection.executemany(
                    "INSERT INTO calendar_dates (service_id, date, exception_type) VALUES (?, ?, ?)",
                    rows)
            connection.executemany(
                "UPDATE trips SET service_id=? WHERE trip_id=?", moves)
        return {
            "journeys_with_copies": len(groups),
            "trips_given_private_calendars": len(moves),
            "calendar_clones": len(clones),
            "same_day_listings_removed": removed_listings,
            "by_operator": dict(sorted(by_operator.items())),
        }
    finally:
        connection.close()
