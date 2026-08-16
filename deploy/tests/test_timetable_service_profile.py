import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

import timetable_service_profile as service_profile
from timetable_service_profile import (
    ServiceProfileError,
    bristol_today,
    compare_databases,
)


START = date(2026, 7, 29)
EXACT_CURRENT = Path(r"C:\tmp\bbb-run-29944744744\timetable.db")
EXACT_CANDIDATE = Path(r"C:\tmp\bbb-run-30421182234\timetable.db")


def make_service_database(path: Path, services: list[dict], *,
                          exceptions: list[tuple[str, str, int]] | None = None):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE agency (
            agency_id TEXT PRIMARY KEY, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
            direction_id INTEGER);
        CREATE TABLE stop_times (
            trip_id TEXT, stop_id TEXT, stop_sequence INTEGER);
        CREATE TABLE calendar (
            service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        CREATE TABLE route_shapes (
            route_name TEXT, operator_noc TEXT, direction_id INTEGER,
            variant INTEGER, points_json TEXT);
    """)
    agencies = sorted({str(item["operator"]) for item in services})
    connection.executemany(
        "INSERT INTO agency VALUES (?, ?)",
        [(f"A{index}", operator) for index, operator in enumerate(agencies)],
    )
    agency_ids = {operator: f"A{index}" for index, operator in enumerate(agencies)}
    for index, item in enumerate(services):
        service_id = str(item.get("service_id", f"S{index}"))
        route_id = f"R{index}"
        operator = str(item["operator"])
        route = str(item["route"])
        direction = int(item.get("direction", 0))
        connection.execute(
            "INSERT INTO routes VALUES (?, ?, ?)",
            (route_id, agency_ids[operator], route),
        )
        weekdays = item.get("weekdays", (1, 1, 1, 1, 1, 1, 1))
        connection.execute(
            "INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (service_id, *weekdays, item.get("start", "20200101"),
             item.get("end", "20271231")),
        )
        trips = int(item.get("trips", 10))
        stops = int(item.get("stops", 10))
        for trip_number in range(trips):
            trip_id = f"T{index}-{trip_number}"
            connection.execute(
                "INSERT INTO trips VALUES (?, ?, ?, ?)",
                (trip_id, route_id, service_id, direction),
            )
            connection.executemany(
                "INSERT INTO stop_times VALUES (?, ?, ?)",
                [(trip_id, f"STOP-{stop}", stop) for stop in range(stops)],
            )
        connection.execute(
            "INSERT INTO route_shapes VALUES (?, ?, ?, 0, ?)",
            (route, operator, direction,
             item.get("shape", "[[51.45,-2.59],[51.46,-2.58]]")),
        )
    connection.executemany(
        "INSERT INTO calendar_dates VALUES (?, ?, ?)", exceptions or [])
    connection.commit()
    connection.close()


def compare(tmp_path: Path, current_services: list[dict],
            candidate_services: list[dict], *,
            current_exceptions=None, candidate_exceptions=None):
    current = tmp_path / "current.db"
    candidate = tmp_path / "candidate.db"
    make_service_database(
        current, current_services, exceptions=current_exceptions)
    make_service_database(
        candidate, candidate_services, exceptions=candidate_exceptions)
    return compare_databases(current, candidate, start_date=START)


def make_duplicate_journey_database(path: Path, *, duplicated: bool):
    """Create production-shaped data with one journey repeated by its source."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE agency (
            agency_id TEXT PRIMARY KEY, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
            trip_headsign TEXT, direction_id INTEGER);
        CREATE TABLE stop_times (
            trip_id TEXT, arrival_time TEXT, departure_time TEXT,
            stop_id TEXT, stop_sequence INTEGER);
        CREATE TABLE calendar (
            service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (
            service_id TEXT, date TEXT, exception_type INTEGER);
        CREATE TABLE route_shapes (
            route_name TEXT, operator_noc TEXT, direction_id INTEGER,
            variant INTEGER, points_json TEXT);
    """)
    connection.execute("INSERT INTO agency VALUES ('A1', 'SSWL')")
    connection.executemany(
        "INSERT INTO routes VALUES (?, 'A1', '505')",
        [("R1",), ("R2",)],
    )
    connection.execute(
        "INSERT INTO calendar VALUES "
        "('S1', 1, 1, 1, 1, 1, 1, 1, '20200101', '20271231')")
    journeys = [
        ("T-0800-A", "R1", "08:00:00", "08:30:00"),
        ("T-0900", "R1", "09:00:00", "09:30:00"),
    ]
    if duplicated:
        journeys.append(("T-0800-B", "R2", "08:00:00", "08:30:00"))
    for trip_id, route_id, first_time, last_time in journeys:
        connection.execute(
            "INSERT INTO trips VALUES (?, ?, 'S1', 'Portishead', 0)",
            (trip_id, route_id),
        )
        connection.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                (trip_id, first_time, first_time, "START", 1),
                (trip_id, last_time, last_time, "FINISH", 2),
            ],
        )
    connection.execute(
        "INSERT INTO route_shapes VALUES "
        "('505', 'SSWL', 0, 0, '[[51.45,-2.59],[51.46,-2.58]]')")
    connection.commit()
    connection.close()


def test_historical_row_shrink_does_not_hide_complete_current_service(tmp_path):
    live = [{"operator": "MAIN", "route": "1", "trips": 10, "stops": 10}]
    candidate = [{"operator": "MAIN", "route": "1", "trips": 10, "stops": 10}]

    result = compare(tmp_path, live, candidate)

    assert result["status"] == "pass"
    assert result["near_term_totals"][1]["ratio"] == 1.0


def test_duplicate_source_journeys_do_not_create_a_false_service_collapse(
        tmp_path):
    current = tmp_path / "duplicated-current.db"
    candidate = tmp_path / "deduplicated-candidate.db"
    make_duplicate_journey_database(current, duplicated=True)
    make_duplicate_journey_database(candidate, duplicated=False)

    result = compare_databases(current, candidate, start_date=START)

    assert result["status"] == "pass"
    assert result["current_profile"]["journey_identity"] == "canonical_schedule"
    assert result["current_profile"]["duplicate_trips_removed"] == 1
    assert result["candidate_profile"]["duplicate_trips_removed"] == 0
    totals = {gate["metric"]: gate for gate in result["near_term_totals"]}
    assert totals["trips"]["ratio"] == 1.0
    assert totals["stop_times"]["ratio"] == 1.0


def test_missing_near_term_day_fails_with_date_and_metric(tmp_path):
    services = [{"operator": "MAIN", "route": "1"}]
    removed = [("S0", START.strftime("%Y%m%d"), 2)]

    with pytest.raises(ServiceProfileError) as failure:
        compare(tmp_path, services, services, candidate_exceptions=removed)

    assert failure.value.code == "candidate_service_collapse"
    assert failure.value.context["date"] == START.isoformat()
    assert failure.value.context["metric"] == "trips"


def test_material_operator_cannot_be_hidden_by_an_unrelated_operator(tmp_path):
    current = [
        {"operator": "MAIN", "route": "1", "trips": 100},
        {"operator": "LOST", "route": "X", "trips": 30,
         "shape": "[[51.40,-2.60],[51.41,-2.61]]"},
    ]
    candidate = [
        {"operator": "MAIN", "route": "1", "trips": 130},
        {"operator": "OTHER", "route": "X", "trips": 30,
         "shape": "[[52.00,-1.00],[52.10,-1.10]]"},
    ]

    with pytest.raises(ServiceProfileError) as failure:
        compare(tmp_path, current, candidate)

    assert failure.value.code == "candidate_operator_collapse"
    assert failure.value.context["operator"] == "LOST"


def test_unrelated_new_routes_cannot_hide_missing_live_routes(tmp_path):
    current = [
        {"operator": "MAIN", "route": f"LIVE-{number}"}
        for number in range(10)
    ]
    candidate = [
        {"operator": "MAIN", "route": f"LIVE-{number}"}
        for number in range(7)
    ] + [
        {"operator": "MAIN", "route": f"NEW-{number}", "trips": 10}
        for number in range(10)
    ]

    with pytest.raises(ServiceProfileError) as failure:
        compare(tmp_path, current, candidate)

    assert failure.value.code == "candidate_service_collapse"
    assert failure.value.context["kind"] == "route_coverage"


def test_compatible_operator_transfer_warns_and_passes(tmp_path):
    shape = "[[51.40,-2.60],[51.41,-2.61]]"
    current = [{"operator": "OLD", "route": "Y2C", "trips": 30,
                "shape": shape}]
    candidate = [{"operator": "NEW", "route": "Y2C", "trips": 30,
                  "shape": shape}]

    result = compare(tmp_path, current, candidate)

    assert result["status"] == "pass"
    assert result["warnings"][0]["code"] == "operator_transfer"
    assert result["warnings"][0]["from_operator"] == "OLD"
    assert result["warnings"][0]["to_operator"] == "NEW"


def test_historical_bulk_without_usable_service_fails(tmp_path):
    current = [{"operator": "MAIN", "route": "1", "trips": 10}]
    candidate = [{"operator": "MAIN", "route": "1", "trips": 1000,
                  "end": "20260728"}]

    with pytest.raises(ServiceProfileError) as failure:
        compare(tmp_path, current, candidate)

    assert failure.value.code == "candidate_service_collapse"


def test_future_coverage_cliff_fails_after_the_near_term_window(tmp_path):
    current = [{"operator": "MAIN", "route": "1", "trips": 10}]
    candidate = [{"operator": "MAIN", "route": "1", "trips": 10,
                  "end": "20260831"}]

    with pytest.raises(ServiceProfileError) as failure:
        compare(tmp_path, current, candidate)

    assert failure.value.code == "candidate_future_coverage_cliff"


def test_calendar_date_addition_and_removal_are_applied(tmp_path):
    no_weekdays = (0, 0, 0, 0, 0, 0, 0)
    services = [{"operator": "MAIN", "route": "1", "weekdays": no_weekdays}]
    addition = [("S0", START.strftime("%Y%m%d"), 1)]

    result = compare(
        tmp_path, services, services,
        current_exceptions=addition, candidate_exceptions=addition)

    first = result["near_term_daily"][0]
    assert first["date"] == START.isoformat()
    assert first["current"] > 0


def test_invalid_exception_and_deadline_have_named_bounded_failures(tmp_path):
    services = [{"operator": "MAIN", "route": "1"}]
    invalid = [("S0", START.strftime("%Y%m%d"), 3)]
    current = tmp_path / "current.db"
    candidate = tmp_path / "candidate.db"
    make_service_database(current, services)
    make_service_database(candidate, services, exceptions=invalid)

    with pytest.raises(ServiceProfileError) as malformed:
        compare_databases(current, candidate, start_date=START)
    assert malformed.value.code == "malformed_calendar"

    with pytest.raises(ServiceProfileError) as timeout:
        compare_databases(
            current, current, start_date=START, deadline_seconds=-1)
    assert timeout.value.code == "database_profile_timeout"


def test_invalid_calendar_weekday_flag_is_rejected(tmp_path):
    services = [{
        "operator": "MAIN",
        "route": "1",
        "weekdays": (2, 1, 1, 1, 1, 1, 1),
    }]
    current = tmp_path / "current.db"
    candidate = tmp_path / "candidate.db"
    make_service_database(current, services)
    make_service_database(candidate, services)

    with pytest.raises(ServiceProfileError) as failure:
        compare_databases(current, candidate, start_date=START)

    assert failure.value.code == "malformed_calendar"


def test_oversized_and_locked_database_fail_with_named_context(
        tmp_path, monkeypatch):
    database = tmp_path / "timetable.db"
    make_service_database(
        database, [{"operator": "MAIN", "route": "1"}])
    monkeypatch.setattr(service_profile, "MAX_DATABASE_BYTES", 1)
    with pytest.raises(ServiceProfileError) as oversized:
        compare_databases(database, database, start_date=START)
    assert oversized.value.code == "database_too_large"

    monkeypatch.setattr(service_profile, "MAX_DATABASE_BYTES", 512 * 1024 * 1024)

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", locked)
    with pytest.raises(ServiceProfileError) as unavailable:
        compare_databases(database, database, start_date=START)
    assert unavailable.value.code == "database_profile_failed"


def test_bristol_local_date_handles_dst_instants():
    before_fallback = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    after_fallback = datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc)
    assert bristol_today(before_fallback) == date(2026, 10, 25)
    assert bristol_today(after_fallback) == date(2026, 10, 25)


@pytest.mark.skipif(
    not EXACT_CURRENT.is_file() or not EXACT_CANDIDATE.is_file(),
    reason="exact seven-day GitHub artifacts are not available locally",
)
def test_exact_22_and_29_july_artifacts_pass_semantic_policy():
    result = compare_databases(
        EXACT_CURRENT,
        EXACT_CANDIDATE,
        start_date=START,
        deadline_seconds=10 * 60,
    )

    assert result["status"] == "pass"
    totals = {gate["metric"]: gate for gate in result["near_term_totals"]}
    assert totals["trips"]["ratio"] == pytest.approx(1.003, abs=0.002)
    assert totals["stop_times"]["ratio"] == pytest.approx(1.003, abs=0.002)
