"""Reviewed, dated corrections for known BODS GTFS faults (planning #128)."""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import source_corrections as sc  # noqa: E402

ENTRY = {"date": "2026-12-01", "reason": "test", "reference": "BSD-1342",
         "approved_by": "Tom", "expires": "2026-12-02"}


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript("""
        CREATE TABLE calendar (service_id TEXT PRIMARY KEY, monday INT, tuesday INT,
            wednesday INT, thursday INT, friday INT, saturday INT, sunday INT,
            start_date TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INT);
        INSERT INTO calendar VALUES
            ('BUGGED',  1,1,1,1,1,0,0, '20261101', '20270801'),  -- runs 24 Nov and 8 Dec
            ('SCHOOL',  1,1,1,1,1,0,0, '20261101', '20270801'),  -- genuinely off 24 Nov too
            ('SATONLY', 0,0,0,0,0,1,0, '20261101', '20270801'),  -- not a Tuesday service
            ('NEWEDN',  1,1,1,1,1,0,0, '20261126', '20270801');  -- starts after 24 Nov
        INSERT INTO calendar_dates VALUES
            ('BUGGED', '20261201', 2), ('BUGGED', '20261225', 2),
            ('SCHOOL', '20261201', 2), ('SCHOOL', '20261124', 2),
            ('SATONLY', '20261201', 2),
            ('NEWEDN', '20261201', 2);
    """)
    return conn


def test_only_the_bank_holiday_style_exclusion_is_removed(tmp_path):
    conn = _db(tmp_path)
    assert sc.apply_corrections(conn, [ENTRY]) == {"2026-12-01": 1}
    left = {row[0] for row in conn.execute(
        "SELECT service_id FROM calendar_dates WHERE date='20261201' AND exception_type=2")}
    assert left == {"SCHOOL", "SATONLY", "NEWEDN"}
    # Other dates are untouched, including Christmas Day.
    assert conn.execute("SELECT COUNT(*) FROM calendar_dates WHERE date='20261225'").fetchone()[0] == 1
    assert conn.execute("SELECT service_id, date, reference FROM source_date_corrections").fetchall() \
        == [("BUGGED", "20261201", "BSD-1342")]


def test_an_expired_correction_refuses_the_build(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps([ENTRY]), encoding="utf-8")
    assert sc.load_corrections(date(2026, 12, 2), path) == [ENTRY]
    with pytest.raises(sc.CorrectionError, match="expired on 2026-12-02; delete it"):
        sc.load_corrections(date(2026, 12, 3), path)


def test_an_entry_must_be_reviewed_and_dated(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps([{**ENTRY, "approved_by": ""}]), encoding="utf-8")
    with pytest.raises(sc.CorrectionError, match="approved_by"):
        sc.load_corrections(date(2026, 11, 11), path)


def test_the_committed_corrections_are_valid_and_name_their_reference():
    entries = sc.load_corrections(date(2026, 11, 11))
    assert [entry["date"] for entry in entries] == ["2026-12-01"]
    assert "BSD-1342" in entries[0]["reference"]
