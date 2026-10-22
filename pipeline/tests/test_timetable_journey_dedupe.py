from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import timetable_journey_dedupe as dedupe
from timetable_duplicate_evidence import active_days

START = date(2026, 10, 2)


def make_db(path: Path, trips, calendars, exceptions=()):
    """trips: (trip_id, operator, route, service_id, departure, vjc)."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE agency (agency_id TEXT PRIMARY KEY, agency_noc TEXT);
        CREATE TABLE routes (route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
                            trip_headsign TEXT, direction_id INTEGER, vehicle_journey_code TEXT);
        CREATE TABLE stop_times (trip_id TEXT, arrival_time TEXT, departure_time TEXT,
                                 stop_id TEXT, stop_sequence INTEGER);
        CREATE TABLE calendar (service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER,
            sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INTEGER);
    """)
    for trip_id, operator, route, service, departure, vjc in trips:
        connection.execute("INSERT OR IGNORE INTO agency VALUES (?, ?)", (operator, operator))
        connection.execute("INSERT OR IGNORE INTO routes VALUES (?, ?, ?)",
                           (f"{operator}-{route}", operator, route))
        connection.execute("INSERT INTO trips VALUES (?, ?, ?, 'Bristol', 0, ?)",
                           (trip_id, f"{operator}-{route}", service, vjc))
        connection.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", [
            (trip_id, departure, departure, "A", 1),
            (trip_id, "11:00:00", "11:00:00", "B", 2)])
    for service, mask, start, end in calendars:
        connection.execute("INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (service, *mask, start, end))
    connection.executemany("INSERT INTO calendar_dates VALUES (?, ?, ?)", exceptions)
    connection.commit()
    connection.close()


def days_by_trip(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    calendars = {row["service_id"]: row for row in connection.execute("SELECT * FROM calendar")}
    exceptions = {}
    for service, day, kind in connection.execute("SELECT * FROM calendar_dates"):
        exceptions.setdefault(service, []).append((day, kind))
    result = {row["trip_id"]: active_days(calendars.get(row["service_id"]),
                                          exceptions.get(row["service_id"], []))
              for row in connection.execute("SELECT trip_id, service_id FROM trips")}
    connection.close()
    return result


DAILY = (1, 1, 1, 1, 1, 1, 1)


def flix_like(tmp_path):
    """One coach journey under three daily calendars that only remove dates."""
    path = tmp_path / "tt.db"
    removals = [("F2", (START + timedelta(days=n)).strftime("%Y%m%d"), 2) for n in range(0, 20, 2)]
    removals += [("F3", (START + timedelta(days=n)).strftime("%Y%m%d"), 2) for n in range(0, 30, 3)]
    make_db(path,
            [("t1", "FLIX", "UK047", "F1", "06:45:00", "VJ1"),
             ("t2", "FLIX", "UK047", "F2", "06:45:00", "VJ2"),
             ("t3", "FLIX", "UK047", "F3", "06:45:00", "VJ3"),
             ("other", "FLIX", "UK047", "F2", "08:00:00", "VJ4")],
            [(sid, DAILY, "20261002", "20261231") for sid in ("F1", "F2", "F3")],
            removals)
    return path


def test_each_journey_runs_once_per_day_and_no_day_is_lost(tmp_path):
    path = flix_like(tmp_path)
    before = days_by_trip(path)

    result = dedupe.collapse_same_day_copies(path)

    after = days_by_trip(path)
    copies = ("t1", "t2", "t3")
    assert set().union(*(after[t] for t in copies)) == set().union(*(before[t] for t in copies))
    for day in set().union(*(after[t] for t in copies)):
        assert sum(day in after[t] for t in copies) == 1
    assert after["other"] == before["other"]  # a different departure is left alone
    assert result["by_operator"] == {"FLIX": 2}


def test_trip_rows_and_live_feed_codes_survive(tmp_path):
    path = flix_like(tmp_path)
    connection = sqlite3.connect(path)
    before = connection.execute(
        "SELECT trip_id, vehicle_journey_code FROM trips ORDER BY trip_id").fetchall()
    stop_rows = connection.execute("SELECT COUNT(*) FROM stop_times").fetchone()
    connection.close()

    dedupe.collapse_same_day_copies(path)

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT trip_id, vehicle_journey_code FROM trips ORDER BY trip_id").fetchall() == before
    assert connection.execute("SELECT COUNT(*) FROM stop_times").fetchone() == stop_rows
    duplicates = connection.execute(
        "SELECT service_id, date, COUNT(*) FROM calendar_dates GROUP BY 1, 2 HAVING COUNT(*) > 1"
    ).fetchall()
    connection.close()
    assert duplicates == []  # the Pi's gate rejects repeated exception rows


def test_copies_on_different_days_are_untouched(tmp_path):
    """Editions that hand over cleanly are not duplicates."""
    path = tmp_path / "tt.db"
    make_db(path,
            [("old", "SSWL", "X5", "OLD", "06:45:00", "a"),
             ("new", "SSWL", "X5", "NEW", "06:45:00", "b")],
            [("OLD", DAILY, "20261002", "20261031"),
             ("NEW", DAILY, "20261101", "20261231")])
    result = dedupe.collapse_same_day_copies(path)
    assert result["trips_given_private_calendars"] == 0


def test_journeys_that_differ_in_any_way_are_untouched(tmp_path):
    path = tmp_path / "tt.db"
    make_db(path,
            [("a", "FBRI", "1", "S1", "06:45:00", "a"),
             ("b", "FBRI", "1", "S2", "06:46:00", "b"),   # different time
             ("c", "FBRI", "2", "S2", "06:45:00", "c"),   # different route
             ("d", "ABUS", "1", "S2", "06:45:00", "d")],  # different operator
            [("S1", DAILY, "20261002", "20261231"), ("S2", DAILY, "20261002", "20261231")])
    assert dedupe.collapse_same_day_copies(path)["trips_given_private_calendars"] == 0


def test_running_twice_changes_nothing_more(tmp_path):
    path = flix_like(tmp_path)
    dedupe.collapse_same_day_copies(path)
    first = days_by_trip(path)
    assert dedupe.collapse_same_day_copies(path)["trips_given_private_calendars"] == 0
    assert days_by_trip(path) == first


def test_exception_only_service_keeps_only_its_unclaimed_dates(tmp_path):
    path = tmp_path / "tt.db"
    make_db(path,
            [("weekly", "FLIX", "UK041", "W", "06:45:00", "a"),
             ("extra", "FLIX", "UK041", "X", "06:45:00", "b")],
            [("W", (1, 0, 0, 0, 0, 0, 0), "20261005", "20261102")],
            [("X", "20261012", 1), ("X", "20261013", 1)])  # a Monday it shares, and a Tuesday
    dedupe.collapse_same_day_copies(path)
    after = days_by_trip(path)
    assert after["extra"] == {date(2026, 10, 13)}
    assert date(2026, 10, 12) in after["weekly"]
