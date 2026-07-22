#!/usr/bin/env python3
"""Precompute the compact stop-search route lookup in a timetable candidate."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def build_stop_routes(database: Path) -> int:
    """Replace ``stop_routes`` from the final, fully merged timetable data."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TABLE IF EXISTS stop_routes")
        connection.execute("""
            CREATE TABLE stop_routes (
                stop_code TEXT NOT NULL,
                route_short_name TEXT NOT NULL,
                PRIMARY KEY (stop_code, route_short_name)
            ) WITHOUT ROWID
        """)
        connection.execute("""
            INSERT INTO stop_routes (stop_code, route_short_name)
            SELECT DISTINCT s.stop_code, r.route_short_name
            FROM stop_times st
            JOIN stops s ON st.stop_id = s.stop_id
            JOIN trips t ON st.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE s.stop_code IS NOT NULL AND s.stop_code != ''
              AND r.route_short_name IS NOT NULL
              AND r.route_short_name != ''
        """)
        count = connection.execute(
            "SELECT COUNT(*) FROM stop_routes").fetchone()[0]
        if count <= 0:
            raise RuntimeError("precomputed stop_routes table is empty")
        missing = connection.execute("""
            SELECT s.stop_code
            FROM stops s
            WHERE s.stop_code IS NOT NULL AND s.stop_code != ''
              AND NOT EXISTS (
                  SELECT 1 FROM stop_routes sr
                  WHERE sr.stop_code = s.stop_code)
            LIMIT 1
        """).fetchone()
        if missing:
            raise RuntimeError(
                f"precomputed stop_routes is missing stop {missing[0]!r}")
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    count = build_stop_routes(args.database)
    print(f"precomputed {count} stop/route pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
