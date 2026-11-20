"""Store each repeated journey's stop times once (planning #151).

First publishes a weekly edition series and BODS keeps every edition in full,
so 76% of stop-time rows repeat a journey already stored for another edition.
This converts a finished timetable to the "shared" layout:

* ``trips`` and every calendar stay exactly as they are (same trip ids, same
  dates, same edition labels), with one new column, ``stop_pattern_id``;
* each distinct sequence of stop-time rows is stored once in
  ``pattern_stop_times``;
* ``stop_times`` becomes a view with the same name and columns, joining the
  two, so every reader's queries are unchanged and return identical rows.

Off by default: the build uses it only when BBB_TIMETABLE_LAYOUT=shared.

    python pipeline/timetable_shared_stop_times.py <timetable.db>     # convert in place
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

LAYOUT_TABLE = "timetable_layout"
SHARED_LAYOUT = "shared-v1"
PATTERN_INDEXES = (
    "CREATE INDEX idx_pattern_stop_times_seq ON pattern_stop_times(pattern_id, stop_sequence)",
    "CREATE INDEX idx_pattern_stop_times_stop ON pattern_stop_times(stop_id)",
    "CREATE INDEX idx_trips_stop_pattern ON trips(stop_pattern_id)",
)


def layout(connection: sqlite3.Connection) -> str:
    """'shared-v1' for a converted file, otherwise 'repeated'."""
    if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (LAYOUT_TABLE,)).fetchone():
        row = connection.execute(
            f"SELECT value FROM {LAYOUT_TABLE} WHERE key = 'stop_times'").fetchone()
        if row:
            return str(row[0])
    return "repeated"


def convert(path: Path) -> dict[str, int]:
    """Convert ``path`` in place. Returns row counts before and after."""
    connection = sqlite3.connect(path)
    try:
        if layout(connection) != "repeated":
            raise RuntimeError("timetable is already in the shared layout")
        columns = [row[1] for row in connection.execute("PRAGMA table_info(stop_times)")]
        if "trip_id" not in columns or "stop_sequence" not in columns:
            raise RuntimeError("stop_times lacks trip_id or stop_sequence")
        shared = [name for name in columns if name != "trip_id"]
        quoted = ", ".join(f'"{name}"' for name in shared)

        patterns: dict[bytes, int] = {}
        trip_pattern: dict[str, int] = {}
        pattern_rows: list[tuple] = []
        current, rows = None, []

        def finish(trip: str, trip_rows: list[tuple]) -> None:
            digest = hashlib.sha256(repr(trip_rows).encode()).digest()
            pattern = patterns.get(digest)
            if pattern is None:
                pattern = patterns[digest] = len(patterns) + 1
                pattern_rows.extend((pattern, *row) for row in trip_rows)
            trip_pattern[trip] = pattern

        before = connection.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0]
        for trip, *row in connection.execute(
                f"SELECT trip_id, {quoted} FROM stop_times ORDER BY trip_id, stop_sequence"):
            if trip != current:
                if current is not None:
                    finish(current, rows)
                current, rows = trip, []
            rows.append(tuple(row))
        if current is not None:
            finish(current, rows)

        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='stop_times'").fetchone()[0]
        column_types = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(stop_times)")}
        with connection:
            connection.execute(
                "CREATE TABLE pattern_stop_times (pattern_id INTEGER NOT NULL, "
                + ", ".join(f'"{name}" {column_types[name]}' for name in shared) + ")")
            connection.executemany(
                f"INSERT INTO pattern_stop_times (pattern_id, {quoted}) "
                f"VALUES ({', '.join('?' * (len(shared) + 1))})", pattern_rows)
            connection.execute("ALTER TABLE trips ADD COLUMN stop_pattern_id INTEGER")
            connection.executemany("UPDATE trips SET stop_pattern_id = ? WHERE trip_id = ?",
                                   [(pattern, trip) for trip, pattern in trip_pattern.items()])
            connection.execute("DROP TABLE stop_times")
            connection.execute(
                "CREATE VIEW stop_times AS SELECT t.trip_id AS trip_id, "
                + ", ".join(f'p."{name}" AS "{name}"' for name in shared)
                + " FROM trips t JOIN pattern_stop_times p ON p.pattern_id = t.stop_pattern_id")
            for statement in PATTERN_INDEXES:
                connection.execute(statement)
            connection.execute(f"CREATE TABLE {LAYOUT_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(f"INSERT INTO {LAYOUT_TABLE} VALUES ('stop_times', ?)", (SHARED_LAYOUT,))
            connection.execute(f"INSERT INTO {LAYOUT_TABLE} VALUES ('original_stop_times_sql', ?)",
                               (definition,))
        connection.execute("ANALYZE")
        connection.commit()
        connection.execute("VACUUM")
        after = connection.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0]
        stored = connection.execute("SELECT COUNT(*) FROM pattern_stop_times").fetchone()[0]
    finally:
        connection.close()
    if after != before:
        raise RuntimeError(f"stop_times view has {after} rows, expected {before}")
    return {"stop_times": before, "stored_rows": stored, "patterns": len(patterns),
            "trips": len(trip_pattern)}


if __name__ == "__main__":
    print(convert(Path(sys.argv[1])))
