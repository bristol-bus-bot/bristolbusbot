#!/usr/bin/env python3
"""Mark retained audit rows that represent a trip's first scheduled stop.

The command is dry-run by default.  It never guesses from the minimum observed
row. Sequence zero is inherently an origin because GTFS stop sequences are
non-negative; non-zero origins are marked only when proved by one of the
supplied timetable databases. Passing both the live and previous timetable
lets retained observations survive a timetable edition change.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_origins(paths: list[Path]) -> tuple[dict[str, int], set[str]]:
    candidates: dict[str, set[int]] = {}
    for path in paths:
        with open_read_only(path) as connection:
            rows = connection.execute(
                "SELECT trip_id, MIN(stop_sequence) FROM stop_times "
                "GROUP BY trip_id")
            for trip_id, sequence in rows:
                if trip_id is not None and sequence is not None:
                    candidates.setdefault(str(trip_id), set()).add(int(sequence))
    conflicts = {trip_id for trip_id, values in candidates.items()
                 if len(values) != 1}
    origins = {trip_id: next(iter(values))
               for trip_id, values in candidates.items()
               if trip_id not in conflicts}
    return origins, conflicts


def backfill(audit_path: Path, timetable_paths: list[Path], *,
             apply: bool = False, minimum_match_pct: float = 0.0) -> dict:
    origins, conflicts = load_origins(timetable_paths)
    connection = sqlite3.connect(audit_path)
    connection.execute("PRAGMA busy_timeout=60000")
    try:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(timepoint_observations)")}
        has_marker = "is_origin" in columns
        selected = ("SELECT trip_id, stop_sequence, is_origin "
                    "FROM timepoint_observations" if has_marker else
                    "SELECT trip_id, stop_sequence, 0 "
                    "FROM timepoint_observations")
        rows = connection.execute(selected).fetchall()
        matched = 0
        already_marked = 0
        would_mark = 0
        sequence_zero_to_mark = 0
        timetable_nonzero_to_mark = 0
        unmatched_trip_ids = set()
        for trip_id, sequence, is_origin in rows:
            expected = origins.get(str(trip_id))
            if expected is None:
                unmatched_trip_ids.add(str(trip_id))
            else:
                matched += 1
            proven_origin = int(sequence) == 0 or (
                expected is not None and int(sequence) == expected)
            if proven_origin:
                if is_origin:
                    already_marked += 1
                else:
                    would_mark += 1
                    if int(sequence) == 0:
                        sequence_zero_to_mark += 1
                    else:
                        timetable_nonzero_to_mark += 1
        match_pct = round(100.0 * matched / len(rows), 2) if rows else 100.0
        result = {
            "mode": "apply" if apply else "dry_run",
            "audit_rows": len(rows),
            "matched_rows": matched,
            "matched_rows_pct": match_pct,
            "unmatched_trip_ids": len(unmatched_trip_ids),
            "conflicting_timetable_trip_ids": len(conflicts),
            "already_marked_origin_rows": already_marked,
            "origin_rows_to_mark": would_mark,
            "sequence_zero_rows_to_mark": sequence_zero_to_mark,
            "timetable_proven_nonzero_origin_rows_to_mark":
                timetable_nonzero_to_mark,
            "origin_rows_marked": 0,
        }
        if apply:
            if match_pct < minimum_match_pct:
                raise RuntimeError(
                    f"only {match_pct}% of audit rows matched supplied "
                    f"timetables; minimum is {minimum_match_pct}%")
            connection.execute("BEGIN IMMEDIATE")
            if not has_marker:
                connection.execute(
                    "ALTER TABLE timepoint_observations ADD COLUMN "
                    "is_origin INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                "CREATE TEMP TABLE origin_map "
                "(trip_id TEXT PRIMARY KEY, stop_sequence INTEGER NOT NULL)")
            connection.executemany(
                "INSERT INTO origin_map VALUES (?, ?)",
                origins.items(),
            )
            connection.execute(
                "UPDATE timepoint_observations SET is_origin = 1 "
                "WHERE is_origin = 0 AND (stop_sequence = 0 OR EXISTS ("
                "SELECT 1 FROM origin_map "
                "WHERE origin_map.trip_id = timepoint_observations.trip_id "
                "AND origin_map.stop_sequence = "
                "timepoint_observations.stop_sequence))")
            result["origin_rows_marked"] = connection.execute(
                "SELECT changes()").fetchone()[0]
            connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, required=True)
    parser.add_argument("--timetable-db", type=Path, action="append",
                        required=True)
    parser.add_argument("--minimum-match-pct", type=float, default=0.0)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = backfill(
        args.audit_db, args.timetable_db, apply=args.apply,
        minimum_match_pct=args.minimum_match_pct)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
