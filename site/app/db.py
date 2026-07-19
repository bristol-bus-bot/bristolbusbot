"""Per-request, read-only access to the live and timetable databases."""
from __future__ import annotations

import sqlite3

from flask import current_app, g


def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def live() -> sqlite3.Connection:
    if "live_db" not in g:
        g.live_db = _open_ro(current_app.config["BBB"].live_db)
    return g.live_db


def gtfs() -> sqlite3.Connection:
    if "gtfs_db" not in g:
        g.gtfs_db = _open_ro(current_app.config["BBB"].timetable_db)
    return g.gtfs_db


def close_all(_exc=None) -> None:
    for key in ("live_db", "gtfs_db"):
        conn = g.pop(key, None)
        if conn is not None:
            conn.close()
