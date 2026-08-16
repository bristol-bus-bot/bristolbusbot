from __future__ import annotations

import sqlite3

import pytest

from deploy.audit_origin_backfill import backfill


def make_timetable(path, trips):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE stop_times (trip_id TEXT, stop_sequence INTEGER)")
    connection.executemany("INSERT INTO stop_times VALUES (?, ?)", trips)
    connection.commit()
    connection.close()


def make_audit(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE timepoint_observations "
        "(trip_id TEXT, stop_sequence INTEGER)")
    connection.executemany("INSERT INTO timepoint_observations VALUES (?, ?)", [
        ("zero-based", 0), ("zero-based", 1),
        ("one-based", 1), ("one-based", 2),
        ("missing", 0),
    ])
    connection.commit()
    connection.close()


def test_dry_run_does_not_change_schema_or_rows(tmp_path):
    audit = tmp_path / "audit.db"
    timetable = tmp_path / "timetable.db"
    make_audit(audit)
    make_timetable(timetable, [
        ("zero-based", 0), ("zero-based", 1),
        ("one-based", 1), ("one-based", 2),
    ])

    result = backfill(audit, [timetable])

    assert result["mode"] == "dry_run"
    assert result["origin_rows_to_mark"] == 3
    assert result["sequence_zero_rows_to_mark"] == 2
    assert result["timetable_proven_nonzero_origin_rows_to_mark"] == 1
    connection = sqlite3.connect(audit)
    assert "is_origin" not in {
        row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
    connection.close()


def test_apply_marks_only_timetable_proven_origins(tmp_path):
    audit = tmp_path / "audit.db"
    timetable = tmp_path / "timetable.db"
    make_audit(audit)
    make_timetable(timetable, [
        ("zero-based", 0), ("zero-based", 1),
        ("one-based", 1), ("one-based", 2),
    ])

    result = backfill(
        audit, [timetable], apply=True, minimum_match_pct=75.0)

    assert result["origin_rows_marked"] == 3
    connection = sqlite3.connect(audit)
    rows = connection.execute(
        "SELECT trip_id, stop_sequence, is_origin "
        "FROM timepoint_observations ORDER BY trip_id, stop_sequence"
    ).fetchall()
    connection.close()
    assert rows == [
        ("missing", 0, 1),
        ("one-based", 1, 1), ("one-based", 2, 0),
        ("zero-based", 0, 1), ("zero-based", 1, 0),
    ]


def test_apply_fails_closed_when_too_few_rows_match(tmp_path):
    audit = tmp_path / "audit.db"
    timetable = tmp_path / "timetable.db"
    make_audit(audit)
    make_timetable(timetable, [("zero-based", 0)])

    with pytest.raises(RuntimeError, match="minimum"):
        backfill(audit, [timetable], apply=True, minimum_match_pct=95.0)
