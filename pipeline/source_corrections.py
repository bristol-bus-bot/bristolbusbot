"""Reviewed, dated corrections for known faults in the BODS GTFS.

Each entry in ``source_corrections.json`` names one service date that the BODS
regional GTFS wrongly treats as a non-operating day for ordinary calendars
(for example BSD-1342: Tuesday 1 December 2026 marked like a bank holiday).
For that date only, a "not running" exception (``exception_type=2``) is
removed from a calendar when the same calendar runs normally on the same
weekday one week before and one week after. Bank-holiday-style exclusions
that BODS added fail that test on the right calendars; genuine one-off
exclusions (school days, works) keep their exception because they are not
this pattern, or because the calendar does not run on the neighbouring weeks.

Every safety check downstream is unchanged. Each removed exception is
recorded in ``source_date_corrections``. An entry stops being valid after its
``expires`` date, and the build then refuses to start until it is deleted, so
a correction can never quietly outlive the fault.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

CORRECTIONS_FILE = Path(__file__).resolve().parent / "source_corrections.json"
REQUIRED = ("date", "reason", "reference", "approved_by", "expires")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")


class CorrectionError(RuntimeError):
    pass


def load_corrections(today: date, path: Path = CORRECTIONS_FILE) -> list[dict]:
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise CorrectionError(f"{path.name} must be a list of corrections")
    for entry in entries:
        missing = [key for key in REQUIRED if not entry.get(key)]
        if missing:
            raise CorrectionError(f"correction is missing {', '.join(missing)}: {entry}")
        day = date.fromisoformat(entry["date"])
        expires = date.fromisoformat(entry["expires"])
        if expires < day:
            raise CorrectionError(f"correction for {day} expires before it applies")
        if today > expires:
            raise CorrectionError(
                f"correction for {day} ({entry['reference']}) expired on {expires}; "
                f"delete it from {path.name}")
    return entries


def apply_corrections(connection: sqlite3.Connection, entries: list[dict]) -> dict[str, int]:
    connection.execute("""CREATE TABLE IF NOT EXISTS source_date_corrections (
        service_id TEXT NOT NULL, date TEXT NOT NULL, reference TEXT NOT NULL,
        PRIMARY KEY (service_id, date))""")
    result = {}
    for entry in entries:
        day = date.fromisoformat(entry["date"])
        result[entry["date"]] = _apply_one(connection, day, entry["reference"])
    return result


def _apply_one(connection: sqlite3.Connection, day: date, reference: str) -> int:
    weekday = WEEKDAYS[day.weekday()]
    before, after = day - timedelta(days=7), day + timedelta(days=7)
    target, prior, following = (value.strftime("%Y%m%d") for value in (day, before, after))
    services = [row[0] for row in connection.execute(f"""
        SELECT x.service_id
          FROM calendar_dates x
          JOIN calendar c ON c.service_id = x.service_id
         WHERE x.date = ? AND x.exception_type = 2
           AND c.{weekday} = 1
           AND c.start_date <= ? AND c.end_date >= ?
           AND NOT EXISTS (SELECT 1 FROM calendar_dates n
                            WHERE n.service_id = x.service_id AND n.exception_type = 2
                              AND n.date IN (?, ?))""",
        (target, prior, following, prior, following))]
    for service_id in services:
        connection.execute(
            "DELETE FROM calendar_dates WHERE service_id = ? AND date = ? AND exception_type = 2",
            (service_id, target))
        connection.execute(
            "INSERT OR REPLACE INTO source_date_corrections VALUES (?, ?, ?)",
            (service_id, target, reference))
    return len(services)
