from __future__ import annotations

import sqlite3

from deploy.audit_final_backfill import backfill, scheduled_utc


def make_audit(path, *, with_column: bool):
    connection = sqlite3.connect(path)
    final_column = ", is_final INTEGER NOT NULL DEFAULT 0" if with_column else ""
    connection.execute(
        "CREATE TABLE timepoint_observations (service_date TEXT, trip_id TEXT, "
        "stop_sequence INTEGER, scheduled_local TEXT, "
        f"is_origin INTEGER NOT NULL DEFAULT 0{final_column})")
    connection.execute(
        "CREATE TABLE expected_trips (service_date TEXT, trip_id TEXT, last_departure TEXT)")
    connection.executemany(
        "INSERT INTO timepoint_observations "
        "(service_date, trip_id, stop_sequence, scheduled_local, is_origin) VALUES (?,?,?,?,?)", [
            ("20261020", "T1", 1, "2026-10-20T10:00:00+01:00", 1),   # origin
            ("20261020", "T1", 5, "2026-10-20T10:20:00+01:00", 0),   # middle
            ("20261020", "T1", 9, "2026-10-20T10:40:00+01:00", 0),   # last stop
            ("20261020", "T2", 9, "2026-10-21T00:30:00+01:00", 0),   # past midnight
            ("20261020", "T3", 4, "2026-10-20T11:00:00+01:00", 0),   # not in snapshot
            ("20261020", "T4", 4, "2026-10-20T11:00:00+01:00", 0),   # no last departure
            ("20260815", "T5", 4, "2026-08-15T11:00:00+01:00", 0),   # before --since
        ])
    connection.executemany("INSERT INTO expected_trips VALUES (?,?,?)", [
        ("20261020", "T1", "10:40:00"),
        ("20261020", "T2", "24:30:00"),
        ("20261020", "T4", None),
        ("20260815", "T5", "11:00:00"),
    ])
    connection.commit()
    connection.close()


def finals(path):
    with sqlite3.connect(path) as connection:
        return sorted(connection.execute(
            "SELECT trip_id, stop_sequence FROM timepoint_observations WHERE is_final = 1"))


def test_dry_run_counts_without_changing_anything(tmp_path):
    path = tmp_path / "audit.db"
    make_audit(path, with_column=False)
    result = backfill(path, since="20260822")
    assert result["mode"] == "dry_run"
    assert result["rows"] == 5                      # origin and pre-since rows skipped
    assert result["final_rows"] == 2 and result["to_mark"] == 2
    assert result["trip_not_in_snapshot"] == 1 and result["no_last_departure"] == 1
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
    assert "is_final" not in columns


def test_apply_marks_only_the_last_stop_and_is_repeatable(tmp_path):
    path = tmp_path / "audit.db"
    make_audit(path, with_column=False)
    assert backfill(path, since="20260822", apply=True, batch_size=1)["marked"] == 2
    assert finals(path) == [("T1", 9), ("T2", 9)]
    again = backfill(path, since="20260822", apply=True)
    assert again["marked"] == 0 and again["already_marked"] == 2


def test_gtfs_times_follow_the_collectors_noon_rule():
    # 25 Oct 2026 is the autumn change: 10:00 on the timetable is 10:00 GMT.
    assert scheduled_utc("20261025", "10:00:00").isoformat() == "2026-10-25T10:00:00+00:00"
    assert scheduled_utc("20261020", "24:30:00").isoformat() == "2026-10-20T23:30:00+00:00"
    assert scheduled_utc("20261020", None) is None
