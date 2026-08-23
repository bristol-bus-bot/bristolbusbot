from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_snapshot  # noqa: E402


def timetable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT, route_id TEXT, service_id TEXT, direction_id INTEGER,
            block_id TEXT, vehicle_journey_code TEXT);
        CREATE TABLE stops (stop_id TEXT, stop_code TEXT);
        CREATE TABLE stop_times (
            trip_id TEXT, departure_time TEXT, stop_id TEXT,
            stop_sequence INTEGER);
        CREATE TABLE calendar (
            service_id TEXT, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        CREATE TABLE route_service_editions (
            route_id TEXT, edition_start TEXT, effective_end TEXT);
        INSERT INTO agency VALUES ('A', 'FBRI');
        INSERT INTO routes VALUES ('R-STABLE', 'A', '75');
        INSERT INTO stops VALUES ('STOP-A', '0100A');
        INSERT INTO stops VALUES ('STOP-B', '0100B');
        INSERT INTO route_service_editions VALUES (
            'R-STABLE', '20260726', '20261231');
    """)
    return connection


def use_databases(monkeypatch, timetable_path: Path, audit_path: Path) -> None:
    monkeypatch.setattr(audit_snapshot, "TIMETABLE_DB", str(timetable_path))
    monkeypatch.setattr(audit_snapshot, "AUDIT_DB", str(audit_path))


def test_ordinary_weekday_keeps_duty_identity_and_after_midnight_trip(
        monkeypatch, tmp_path):
    timetable_path = tmp_path / "timetable.db"
    audit_path = tmp_path / "audit.db"
    connection = timetable(timetable_path)
    connection.executescript("""
        INSERT INTO calendar VALUES (
            'WEEKDAY', 1, 1, 1, 1, 1, 0, 0, '20260726', '20261231');
        INSERT INTO trips VALUES (
            'ZERO', 'R-STABLE', 'WEEKDAY', 0, 'BLOCK-75-A', 'VJC-100');
        INSERT INTO trips VALUES (
            'ONE', 'R-STABLE', 'WEEKDAY', 1, 'BLOCK-75-B', 'VJC-101');
        INSERT INTO trips VALUES (
            'NIGHT', 'R-STABLE', 'WEEKDAY', 0, 'BLOCK-75-N', 'VJC-102');
        INSERT INTO stop_times VALUES ('ZERO', '08:00:00', 'STOP-A', 0);
        INSERT INTO stop_times VALUES ('ZERO', '08:30:00', 'STOP-B', 1);
        INSERT INTO stop_times VALUES ('ONE', '09:00:00', 'STOP-B', 1);
        INSERT INTO stop_times VALUES ('ONE', '09:30:00', 'STOP-A', 2);
        INSERT INTO stop_times VALUES ('NIGHT', '25:10:00', 'STOP-A', 0);
        INSERT INTO stop_times VALUES ('NIGHT', '25:40:00', 'STOP-B', 1);
    """)
    connection.commit()
    connection.close()
    use_databases(monkeypatch, timetable_path, audit_path)

    assert audit_snapshot.build_snapshot("20260814") == 3

    result = sqlite3.connect(audit_path).execute(
        """SELECT trip_id, first_departure, siri_ref, route_id, service_id,
                  block_id, vehicle_journey_code, first_stop_id,
                  first_stop_code, timetable_edition, last_departure
             FROM expected_trips ORDER BY trip_id""").fetchall()
    assert result == [
        ("NIGHT", "25:10:00", "0110", "R-STABLE", "WEEKDAY",
         "BLOCK-75-N", "VJC-102", "STOP-A", "0100A", "20260726",
         "25:40:00"),
        ("ONE", "09:00:00", "0900", "R-STABLE", "WEEKDAY",
         "BLOCK-75-B", "VJC-101", "STOP-B", "0100B", "20260726",
         "09:30:00"),
        ("ZERO", "08:00:00", "0800", "R-STABLE", "WEEKDAY",
         "BLOCK-75-A", "VJC-100", "STOP-A", "0100A", "20260726",
         "08:30:00"),
    ]
    assert audit_snapshot.build_snapshot("20260814") == 3
    assert sqlite3.connect(audit_path).execute(
        "SELECT COUNT(*) FROM expected_trips").fetchone()[0] == 3


def test_calendar_exception_removes_regular_trip_and_adds_special_trip(
        monkeypatch, tmp_path):
    timetable_path = tmp_path / "timetable.db"
    audit_path = tmp_path / "audit.db"
    connection = timetable(timetable_path)
    connection.executescript("""
        INSERT INTO calendar VALUES (
            'REGULAR', 1, 1, 1, 1, 1, 0, 0, '20260726', '20261231');
        INSERT INTO trips VALUES (
            'REGULAR-TRIP', 'R-STABLE', 'REGULAR', 0,
            'BLOCK-REGULAR', 'VJC-REGULAR');
        INSERT INTO trips VALUES (
            'SPECIAL-TRIP', 'R-STABLE', 'SPECIAL', 1,
            'BLOCK-SPECIAL', 'VJC-SPECIAL');
        INSERT INTO stop_times VALUES (
            'REGULAR-TRIP', '10:00:00', 'STOP-A', 0);
        INSERT INTO stop_times VALUES (
            'SPECIAL-TRIP', '11:00:00', 'STOP-B', 0);
        INSERT INTO calendar_dates VALUES ('REGULAR', '20260817', 2);
        INSERT INTO calendar_dates VALUES ('SPECIAL', '20260817', 1);
    """)
    connection.commit()
    connection.close()
    use_databases(monkeypatch, timetable_path, audit_path)

    assert audit_snapshot.build_snapshot("20260817") == 1

    assert sqlite3.connect(audit_path).execute(
        "SELECT trip_id, service_id, first_stop_code FROM expected_trips"
    ).fetchall() == [("SPECIAL-TRIP", "SPECIAL", "0100B")]


def test_snapshot_without_route_editions_keeps_explicit_blank_version(
        monkeypatch, tmp_path):
    timetable_path = tmp_path / "timetable.db"
    audit_path = tmp_path / "audit.db"
    connection = timetable(timetable_path)
    connection.executescript("""
        DROP TABLE route_service_editions;
        INSERT INTO calendar VALUES (
            'WEEKDAY', 1, 1, 1, 1, 1, 0, 0, '20260726', '20261231');
        INSERT INTO trips VALUES (
            'TRIP', 'R-STABLE', 'WEEKDAY', 0, 'BLOCK', 'VJC');
        INSERT INTO stop_times VALUES ('TRIP', '08:00:00', 'STOP-A', 0);
    """)
    connection.commit()
    connection.close()
    use_databases(monkeypatch, timetable_path, audit_path)

    assert audit_snapshot.build_snapshot("20260814") == 1
    assert sqlite3.connect(audit_path).execute(
        "SELECT timetable_edition FROM expected_trips").fetchone()[0] is None


def test_imported_identifiers_are_bounded():
    assert audit_snapshot.bounded_text("x" * 300) == "x" * 256
    assert audit_snapshot.bounded_text("2026072600", 8) == "20260726"
    assert audit_snapshot.bounded_text(None) is None


def create_legacy_expected_trips(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE expected_trips (
            service_date TEXT NOT NULL, operator TEXT NOT NULL, route TEXT,
            trip_id TEXT NOT NULL, siri_ref TEXT, direction INTEGER,
            first_departure TEXT,
            PRIMARY KEY (service_date, trip_id));
        CREATE INDEX idx_expected_date_route
            ON expected_trips (service_date, operator, route);
    """)


def test_existing_database_is_upgraded_without_changing_old_rows(tmp_path):
    path = tmp_path / "audit.db"
    connection = sqlite3.connect(path)
    create_legacy_expected_trips(connection)
    connection.execute(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?)",
        ("20260801", "FBRI", "75", "OLD-TRIP", "0800", 0, "08:00:00"),
    )
    connection.commit()

    audit_snapshot.init_expected_table(connection)

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(expected_trips)")}
    assert set(audit_snapshot.EXPECTED_TRIP_DETAIL_COLUMNS) <= columns
    assert connection.execute(
        """SELECT trip_id, route_id, service_id, block_id,
                  vehicle_journey_code, first_stop_id, first_stop_code,
                  timetable_edition, last_departure
             FROM expected_trips"""
    ).fetchone() == (
        "OLD-TRIP", None, None, None, None, None, None, None, None)
    connection.close()


def database_bytes(path: Path) -> int:
    connection = sqlite3.connect(path)
    connection.execute("VACUUM")
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    connection.close()
    return page_size * page_count


def test_added_detail_stays_within_measured_pi_storage_budget(tmp_path):
    """8,000 rows is slightly above the measured production daily average."""
    legacy_path = tmp_path / "legacy.db"
    enriched_path = tmp_path / "enriched.db"
    legacy = sqlite3.connect(legacy_path)
    enriched = sqlite3.connect(enriched_path)
    create_legacy_expected_trips(legacy)
    audit_snapshot.init_expected_table(enriched)
    legacy_rows = []
    enriched_rows = []
    for number in range(8000):
        trip_id = f"VJ-2026-08-22-{number:025d}"
        base = ("20260822", "FBRI", "75", trip_id, "0800", number % 2,
                "08:00:00")
        legacy_rows.append(base)
        enriched_rows.append(base + (
            f"R{number % 10000:04d}", f"SERVICE{number % 1000:05d}",
            f"BLOCK-{number:031d}", f"VJ{number:04d}",
            f"STOP-{number % 100000:06d}", f"{number % 1000000:06d}",
            "20260726", "09:35:00",
        ))
    legacy.executemany(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?)", legacy_rows)
    enriched.executemany(
        "INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        enriched_rows)
    legacy.commit()
    enriched.commit()
    legacy.close()
    enriched.close()

    daily_growth = database_bytes(enriched_path) - database_bytes(legacy_path)

    assert daily_growth <= 1_000_000
    assert daily_growth * 95 <= 100 * 1024 * 1024
