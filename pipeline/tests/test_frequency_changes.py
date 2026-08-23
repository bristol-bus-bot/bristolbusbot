from __future__ import annotations

from datetime import date
import json
import sqlite3
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import frequency_changes as frequency  # noqa: E402


BASELINE = frequency.Period(date(2026, 5, 11), date(2026, 6, 7), "term time")
CURRENT = frequency.Period(date(2026, 7, 6), date(2026, 8, 2), "term time")


def database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE expected_trips (
            service_date TEXT NOT NULL,
            operator TEXT NOT NULL,
            route TEXT,
            trip_id TEXT NOT NULL,
            direction INTEGER,
            route_id TEXT,
            PRIMARY KEY (service_date, trip_id)
        )
    """)
    return connection


def add_journeys(
        connection: sqlite3.Connection,
        day: date,
        operator: str,
        route: str,
        route_id: str | None,
        direction: int | None,
        count: int,
) -> None:
    existing = connection.execute(
        "SELECT COUNT(*) FROM expected_trips WHERE service_date=? "
        "AND operator=? AND route_id IS ? AND direction IS ?",
        (frequency.compact(day), operator, route_id, direction),
    ).fetchone()[0]
    for number in range(count):
        connection.execute(
            "INSERT INTO expected_trips VALUES (?,?,?,?,?,?)",
            (
                frequency.compact(day), operator, route,
                f"{frequency.compact(day)}-{operator}-{route_id}-{direction}-"
                f"{existing + number}",
                direction, route_id,
            ),
        )


def populate_complete_comparison(connection: sqlite3.Connection) -> None:
    for period, counts in (
        (BASELINE, {("RID-1", 0): 10, ("RID-1", 1): 4, ("RID-OTHER", 0): 3}),
        (CURRENT, {("RID-1", 0): 8, ("RID-1", 1): 5, ("RID-OTHER", 0): 3}),
    ):
        for day in period.weekdays:
            for (route_id, direction), count in counts.items():
                add_journeys(
                    connection, day, "FBRI", "1", route_id, direction, count)
    connection.commit()


def result(connection: sqlite3.Connection, **kwargs) -> dict:
    return frequency.compare_periods(
        connection, BASELINE, CURRENT, include_unchanged=True, **kwargs)


def change_by_identity(comparison: dict) -> dict[tuple[str, int], dict]:
    return {
        (row["route_id"], row["direction"]): row
        for row in comparison["changes"]
    }


def test_comparison_keeps_registration_and_direction_separate(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)

    comparison = result(connection)

    assert comparison["available"] is True
    changes = change_by_identity(comparison)
    assert changes[("RID-1", 0)]["baseline_weekday_journeys"] == 50
    assert changes[("RID-1", 0)]["current_weekday_journeys"] == 40
    assert changes[("RID-1", 0)]["journey_change"] == -10
    assert changes[("RID-1", 0)]["percentage_change"] == -20.0
    assert changes[("RID-1", 1)]["journey_change"] == 5
    assert changes[("RID-OTHER", 0)]["journey_change"] == 0
    assert comparison["calendar_context_verified"] is True


def test_bank_holiday_and_network_exception_dates_are_visible_and_excluded(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)
    exception_day = date(2026, 7, 14)
    connection.execute(
        "DELETE FROM expected_trips WHERE service_date=? AND route_id='RID-OTHER'",
        (frequency.compact(exception_day),),
    )
    # A known bank holiday does not need a snapshot at all: it is outside the
    # ordinary weekday sample and must still appear in the exclusion receipt.
    connection.execute(
        "DELETE FROM expected_trips WHERE service_date='20260525'")
    connection.commit()

    comparison = result(connection)

    assert comparison["available"] is True
    baseline_excluded = comparison["baseline"]["excluded_dates"]
    current_excluded = comparison["current"]["excluded_dates"]
    assert {row["date"] for row in baseline_excluded} == {"20260525"}
    assert "bank holiday" in baseline_excluded[0]["reason"]
    assert {row["date"] for row in current_excluded} == {"20260714"}
    assert "repeated Tuesday pattern" in current_excluded[0]["reason"]
    assert comparison["baseline"]["usable_days"] == 19
    assert comparison["current"]["usable_days"] == 19


def test_route_varying_inside_period_is_withheld_not_averaged(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)
    first_monday = frequency.compact(CURRENT.start)
    trip = connection.execute(
        "SELECT trip_id FROM expected_trips WHERE service_date=? "
        "AND route_id='RID-1' AND direction=0 LIMIT 1",
        (first_monday,),
    ).fetchone()[0]
    connection.execute("DELETE FROM expected_trips WHERE trip_id=?", (trip,))
    add_journeys(connection, CURRENT.start, "FBRI", "1", "RID-OTHER", 0, 1)
    connection.commit()

    comparison = result(connection)

    withheld = {
        (row["route_id"], row["direction"])
        for row in comparison["unstable_routes_withheld"]
    }
    assert ("RID-1", 0) in withheld
    assert ("RID-OTHER", 0) in withheld
    changes = change_by_identity(comparison)
    assert ("RID-1", 0) not in changes
    assert ("RID-OTHER", 0) not in changes


def test_period_without_repeated_weekday_pattern_is_unavailable(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)
    for index, monday in enumerate(
            day for day in CURRENT.weekdays if day.weekday() == 0):
        add_journeys(connection, monday, "FBRI", "X", "RID-X", 0, index)
    connection.commit()

    comparison = result(connection)

    assert comparison["available"] is False
    assert "no repeated ordinary Monday network pattern" in comparison["reason"]


def test_legacy_rows_without_route_identity_fail_closed(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)
    connection.execute(
        "UPDATE expected_trips SET route_id=NULL WHERE service_date='20260602'")
    connection.commit()

    comparison = result(connection)

    assert comparison["available"] is False
    assert "without complete route ID and direction: 20260602" in comparison["reason"]


def test_auto_report_names_unavailable_one_three_six_and_twelve_months(tmp_path):
    connection = database(tmp_path / "audit.db")
    period = frequency.Period(date(2026, 8, 3), date(2026, 8, 30))
    for day in period.weekdays:
        add_journeys(connection, day, "FBRI", "1", "RID-1", 0, 4)
    connection.commit()

    report = frequency.comparison_report(
        connection,
        as_of=date(2026, 8, 30),
        weeks=4,
        horizons=(1, 3, 6, 12),
        explicit_periods=None,
        manual_exclusions={},
        include_unchanged=False,
    )

    assert [item["label"] for item in report["comparisons"]] == [
        "1 month", "3 months", "6 months", "12 months"]
    assert all(item["available"] is False for item in report["comparisons"])
    assert report["history"]["first_route_identity_date"] == "20260803"


def test_cli_json_is_repeatable_and_machine_readable(tmp_path, capsys):
    path = tmp_path / "audit.db"
    connection = database(path)
    populate_complete_comparison(connection)
    connection.close()

    exit_code = frequency.main([
        "--audit-db", str(path),
        "--baseline-start", "20260511", "--baseline-end", "20260607",
        "--current-start", "20260706", "--current-end", "20260802",
        "--baseline-context", "term time", "--current-context", "term time",
        "--format", "json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    comparison = payload["comparisons"][0]
    assert comparison["label"] == "explicit periods"
    assert comparison["available"] is True
    assert comparison["calendar_context_verified"] is True
    assert {row["route_id"] for row in comparison["changes"]} == {"RID-1"}


def test_periods_must_be_complete_equal_week_groups(tmp_path):
    connection = database(tmp_path / "audit.db")
    populate_complete_comparison(connection)
    too_short = frequency.Period(date(2026, 6, 1), date(2026, 6, 7))

    try:
        frequency.compare_periods(connection, too_short, CURRENT)
    except ValueError as exc:
        assert "at least 2 complete weeks" in str(exc)
    else:
        raise AssertionError("one-week comparison should be refused")


def test_standard_bank_holiday_dates_cover_substitutes_and_easter():
    holidays = frequency.england_wales_bank_holidays(2026)

    assert holidays[date(2026, 4, 3)] == "Good Friday bank holiday"
    assert holidays[date(2026, 4, 6)] == "Easter Monday bank holiday"
    assert holidays[date(2026, 8, 31)] == "Summer bank holiday"
    assert holidays[date(2026, 12, 28)] == "Boxing Day bank holiday"
