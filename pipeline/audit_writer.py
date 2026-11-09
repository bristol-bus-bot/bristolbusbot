"""Open audit.db for writing with the settings every writer must share.

The collector writes audit.db every 30 seconds, so jobs wait for the lock
rather than fail. ``synchronous`` is a per-connection setting (it is not stored
in the file like ``journal_mode``), so each writer has to set it: NORMAL in WAL
mode cannot corrupt the database; a power cut can lose only the commits since
the last checkpoint. The collector applies the same settings in
``collector/src/collector/audit_db.py``.
"""
from __future__ import annotations

import sqlite3

DEFAULT_BUSY_TIMEOUT_S = 120


def connect(path, busy_timeout_s: float = DEFAULT_BUSY_TIMEOUT_S) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=busy_timeout_s)
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_s * 1000)}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
