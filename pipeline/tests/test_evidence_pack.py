from __future__ import annotations

from datetime import date, timedelta
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import evidence_pack  # noqa: E402


def database(path: Path, area: str = "South Gloucestershire") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE daily_overall_summary (
               service_date TEXT, operator TEXT
           );
           CREATE TABLE daily_geo_summary (
               service_date TEXT, operator TEXT, geo_type TEXT, geo_key TEXT,
               readings_in_gate INTEGER, on_time INTEGER, on_time_pct REAL,
               mean_delay_s INTEGER, median_delay_s INTEGER
           );
           CREATE TABLE daily_geo_route_summary (
               service_date TEXT, operator TEXT, source_operator TEXT,
               geo_type TEXT, geo_key TEXT, route TEXT,
               readings_in_gate INTEGER, on_time INTEGER,
               early INTEGER, late INTEGER, on_time_pct REAL,
               mean_delay_s INTEGER, median_delay_s INTEGER
           );
           CREATE TABLE daily_route_summary (
               service_date TEXT, operator TEXT, route TEXT,
               readings_in_gate INTEGER, on_time INTEGER
           );
           CREATE TABLE expected_trips (
               service_date TEXT, operator TEXT, route TEXT, trip_id TEXT,
               direction INTEGER, route_id TEXT
           );"""
    )
    day = date(2026, 2, 1)
    while day <= date(2026, 7, 31):
        key = day.strftime("%Y%m%d")
        if day.month <= 4:
            on_time = 8
        elif day.month == 5:
            on_time = 8
        elif day.month == 6:
            on_time = 7
        else:
            on_time = 6
        connection.execute(
            "INSERT INTO daily_overall_summary VALUES (?,?)", (key, "ALL"))
        connection.execute(
            "INSERT INTO daily_geo_summary VALUES (?,?,?,?,?,?,?,?,?)",
            (key, "ALL", "area", area, 10, on_time, on_time * 10.0,
             100, 90),
        )
        for route, readings, route_on_time in (
                ("42", 6, min(on_time, 5)), ("43", 4, max(0, on_time - 5))):
            connection.execute(
                "INSERT INTO daily_geo_route_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, "ALL", "FBRI", "area", area, route, readings, route_on_time,
                 0, readings - route_on_time,
                 round(100 * route_on_time / readings, 1), 100, 90),
            )
            connection.execute(
                "INSERT INTO daily_route_summary VALUES (?,?,?,?,?)",
                (key, "ALL", route, readings, route_on_time),
            )
        day += timedelta(days=1)
    connection.execute(
        "INSERT INTO daily_overall_summary VALUES (?,?)", ("20260820", "ALL"))
    connection.commit()
    return connection


def args(**overrides):
    values = {
        "area": "South Gloucestershire",
        "ward": None,
        "route": None,
        "operator": "ALL",
    }
    values.update(overrides)
    return type("Args", (), values)


def report(connection: sqlite3.Connection, scope=None) -> dict:
    scope = scope or evidence_pack.Scope("area", ("South Gloucestershire",))
    return evidence_pack.build_report(
        connection,
        scope,
        "ALL",
        date(2026, 9, 18),
        3,
        None,
        200,
        None,
        evidence_pack.PUBLIC_BASE_URL,
    )


def test_default_period_uses_last_three_complete_months(tmp_path):
    connection = database(tmp_path / "audit.db")

    payload = report(connection)

    assert payload["period"] == {"start": "2026-05-01", "end": "2026-07-31"}
    assert payload["previous_period"] == {
        "start": "2026-02-01", "end": "2026-04-30"}
    assert [row["month_key"] for row in payload["monthly"]] == [
        "2026-05", "2026-06", "2026-07"]
    assert payload["headline"]["readings"] == 920
    assert payload["headline"]["on_time_pct"] == 70.0
    assert payload["previous"]["on_time_pct"] == 80.0
    assert payload["change_from_previous_pct_points"] is None
    assert "audit method changed" in payload["change_unavailable_reason"]
    assert payload["comparability_breaks"] == [{
        "date": "2026-07-13",
        "reason": (
            "the replacement collector changed timetable matching and "
            "stale-position handling"),
    }]
    assert payload["target"]["current_target_pct"] == 87
    assert payload["frequency"]["available"] is False
    assert "calendar" in payload["frequency"]["reason"]
    assert len(payload["questions"]) == 3
    assert "70.0%" in payload["questions"][0]
    assert "920" in payload["questions"][0]
    assert "lost-mileage" in payload["questions"][2]


def test_area_routes_are_ordered_and_small_samples_are_explicit(tmp_path):
    connection = database(tmp_path / "audit.db")

    payload = report(connection)

    rows = payload["routes"]["rows"]
    assert [row["route"] for row in rows] == ["43", "42"]
    assert rows[0]["readings"] == 368
    assert all(row["thin_sample"] is False for row in rows)
    assert payload["routes"]["complete_period"] is True
    assert rows[0]["display"] == "43 (First Bristol)"
    assert "First Bristol route 43" in payload["questions"][1]


def test_partly_covered_route_is_not_used_as_a_full_period_result(tmp_path):
    connection = database(tmp_path / "audit.db")
    connection.execute(
        "DELETE FROM daily_geo_route_summary "
        "WHERE route='43' AND service_date < '20260601'")
    connection.commit()

    payload = report(connection)
    route = next(row for row in payload["routes"]["rows"] if row["route"] == "43")
    rendered = evidence_pack.render_html(payload)

    assert route["partial_period"] is True
    assert "Partial period" in rendered
    assert "First Bristol route 43" not in payload["questions"][1]


def test_target_change_during_period_is_labelled_not_flattened(tmp_path):
    connection = database(tmp_path / "audit.db")

    payload = evidence_pack.build_report(
        connection,
        evidence_pack.Scope("area", ("South Gloucestershire",)),
        "ALL",
        date(2026, 5, 20),
        3,
        None,
        200,
        None,
        evidence_pack.PUBLIC_BASE_URL,
    )
    rendered = evidence_pack.render_html(payload)

    assert payload["period"] == {"start": "2026-02-01", "end": "2026-04-30"}
    assert payload["target"]["period_target_consistent"] is False
    assert payload["target"]["period_start_target_pct"] == 85
    assert payload["target"]["current_target_pct"] == 87
    assert "target changed during this period" in rendered
    assert "How does WECA assess performance across that change?" in payload[
        "questions"][0]


def test_html_escapes_scope_and_pdf_is_readable(tmp_path):
    area = '<script>alert("x")</script>'
    connection = database(tmp_path / "audit.db", area=area)
    payload = report(connection, evidence_pack.Scope("area", (area,)))

    output = evidence_pack.write_pack(payload, tmp_path / "packs")

    html_text = (output / "index.html").read_text(encoding="utf-8")
    assert '<script>alert("x")</script>' not in html_text
    assert "&lt;script&gt;" in html_text
    assert json.loads((output / "data.json").read_text(encoding="utf-8"))[
        "headline"]["readings"] == 920
    pdf = output / "briefing.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
    assert "INDEPENDENT LOCAL BUS EVIDENCE" in extracted
    assert "Three questions to take into the meeting" in extracted
    assert "not cancellations" in extracted


def test_dated_pack_is_immutable_without_explicit_replace(tmp_path):
    connection = database(tmp_path / "audit.db")
    payload = report(connection)
    output_root = tmp_path / "packs"
    evidence_pack.write_pack(payload, output_root)

    try:
        evidence_pack.write_pack(payload, output_root)
    except FileExistsError as exc:
        assert "immutable" in str(exc)
    else:
        raise AssertionError("an existing dated pack should not be overwritten")


def test_scope_names_are_resolved_case_insensitively(tmp_path):
    connection = database(tmp_path / "audit.db")

    area = evidence_pack.resolve_scope(
        connection, args(area="south gloucestershire"))
    routes = evidence_pack.resolve_scope(
        connection, args(area=None, route=["42", "43", "42"]))

    assert area == evidence_pack.Scope("area", ("South Gloucestershire",))
    assert routes == evidence_pack.Scope("routes", ("42", "43"))


def test_incomplete_current_month_is_not_presented_as_complete():
    period = evidence_pack.complete_period(
        date(2026, 8, 20), date(2026, 9, 18), 3)
    assert period[:2] == (date(2026, 5, 1), date(2026, 7, 31))

    complete = evidence_pack.complete_period(
        date(2026, 8, 31), date(2026, 9, 18), 3)
    assert complete[:2] == (date(2026, 6, 1), date(2026, 8, 31))


def test_partly_covered_headline_period_is_rejected(tmp_path):
    connection = database(tmp_path / "audit.db")
    connection.execute(
        "DELETE FROM daily_geo_summary "
        "WHERE service_date BETWEEN '20260501' AND '20260531'")
    connection.commit()

    with pytest.raises(evidence_pack.PackUnavailable, match=(
            "choose a shorter complete window rather than publishing a partial headline")):
        report(connection)


def test_pack_still_renders_before_route_geography_history_exists(tmp_path):
    connection = database(tmp_path / "audit.db")
    connection.execute("DROP TABLE daily_geo_route_summary")
    connection.commit()

    payload = report(connection)
    rendered = evidence_pack.render_html(payload)

    assert payload["routes"]["available"] is False
    assert payload["routes"]["minimum_readings"] == 200
    assert "Route evidence is incomplete" in rendered
    assert "No route-level result is available" in rendered
