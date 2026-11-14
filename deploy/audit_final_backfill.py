#!/usr/bin/env python3
"""Mark retained audit rows taken at a trip's last timing point (planning #131).

Dry run by default. A row is final when its scheduled time is the trip's
last scheduled departure, as recorded that day in ``expected_trips``
(``last_departure``, kept from 22 August 2026). Rows whose trip is missing
from that day's snapshot, or whose snapshot has no last departure, are left
unmarked and counted, never guessed. The collector marks new readings itself.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def gtfs_seconds(value: str | None) -> int | None:
    try:
        parts = str(value).split(":")
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except (TypeError, ValueError, IndexError):
        return None
    if hours < 0 or not (0 <= minutes <= 59 and 0 <= seconds <= 59):
        return None
    return hours * 3600 + minutes * 60 + seconds


def scheduled_utc(service_date: str, gtfs_time: str | None) -> datetime | None:
    """The collector's rule: GTFS times run from noon minus 12 hours."""
    seconds = gtfs_seconds(gtfs_time)
    if seconds is None:
        return None
    day = datetime.strptime(service_date, "%Y%m%d")
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=LONDON)
    return noon.astimezone(timezone.utc) - timedelta(hours=12) + timedelta(seconds=seconds)


def stored_utc(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def classify(connection: sqlite3.Connection, since: str) -> tuple[list[tuple], Counter]:
    """Rows to mark, and counts of what was found."""
    rows = connection.execute(
        """SELECT o.service_date, o.trip_id, o.stop_sequence, o.scheduled_local,
                  o.is_final, e.last_departure, e.trip_id IS NOT NULL
             FROM timepoint_observations o
             LEFT JOIN expected_trips e
               ON e.service_date = o.service_date AND e.trip_id = o.trip_id
            WHERE o.service_date >= ? AND COALESCE(o.is_origin, 0) = 0""",
        (since,)).fetchall()
    counts: Counter = Counter()
    to_mark = []
    for service_date, trip_id, sequence, scheduled, is_final, last, in_snapshot in rows:
        counts["rows"] += 1
        if not in_snapshot:
            counts["trip_not_in_snapshot"] += 1
            continue
        last_utc = scheduled_utc(service_date, last)
        observed_utc = stored_utc(scheduled)
        if last_utc is None or observed_utc is None:
            counts["no_last_departure"] += 1
            continue
        if observed_utc != last_utc:
            continue
        counts["final_rows"] += 1
        if is_final:
            counts["already_marked"] += 1
        else:
            to_mark.append((service_date, trip_id, sequence))
    counts["to_mark"] = len(to_mark)
    return to_mark, counts


def backfill(audit_path: Path, *, since: str, apply: bool = False,
             batch_size: int = 5000) -> dict:
    connection = sqlite3.connect(audit_path, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA synchronous=NORMAL")
    try:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
        if "is_final" not in columns:
            # SQLite DDL is transactional: a dry run adds the column, counts,
            # then rolls back.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "ALTER TABLE timepoint_observations ADD COLUMN "
                "is_final INTEGER NOT NULL DEFAULT 0")
            if not apply:
                # Classify against the new column, then leave the file untouched.
                to_mark, counts = classify(connection, since)
                connection.rollback()
                return _result(counts, apply, marked=0)
            connection.commit()
        to_mark, counts = classify(connection, since)
        marked = 0
        if apply and to_mark:
            # Key order touches pages in sequence; small transactions keep
            # the collector's waits for the write lock short.
            to_mark.sort()
            for start in range(0, len(to_mark), batch_size):
                batch = to_mark[start:start + batch_size]
                connection.execute("BEGIN IMMEDIATE")
                connection.executemany(
                    "UPDATE timepoint_observations SET is_final = 1 "
                    "WHERE service_date = ? AND trip_id = ? AND stop_sequence = ?",
                    batch)
                connection.commit()
                marked += len(batch)
        return _result(counts, apply, marked)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _result(counts: Counter, apply: bool, marked: int) -> dict:
    rows = counts["rows"]
    unclassed = counts["trip_not_in_snapshot"] + counts["no_last_departure"]
    return {
        "mode": "apply" if apply else "dry_run",
        "rows": rows,
        "final_rows": counts["final_rows"],
        "already_marked": counts["already_marked"],
        "to_mark": counts["to_mark"],
        "marked": marked,
        "trip_not_in_snapshot": counts["trip_not_in_snapshot"],
        "no_last_departure": counts["no_last_departure"],
        "unclassed_pct": round(100.0 * unclassed / rows, 2) if rows else 0.0,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, required=True)
    parser.add_argument("--since", default="20260822",
                        help="first service day to mark (YYYYMMDD)")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(json.dumps(backfill(args.audit_db, since=args.since, apply=args.apply),
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
