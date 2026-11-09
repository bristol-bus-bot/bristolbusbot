"""Every audit.db writer uses WAL with synchronous=NORMAL and a busy wait (#96)."""
import re
import sqlite3
from pathlib import Path

import audit_rollup
import audit_writer

PIPELINE = Path(__file__).resolve().parents[1]


def _settings(conn):
    return (conn.execute("PRAGMA synchronous").fetchone()[0],
            conn.execute("PRAGMA busy_timeout").fetchone()[0])


def test_shared_writer_settings(tmp_path):
    conn = audit_writer.connect(tmp_path / "audit.db")
    assert _settings(conn) == (1, 120_000)


def test_rollup_connection_uses_them(tmp_path):
    conn = audit_rollup.connect_audit_db(tmp_path / "audit.db")
    assert _settings(conn) == (1, audit_rollup.SQLITE_BUSY_TIMEOUT_MS)


def test_no_pipeline_job_opens_audit_db_for_writing_directly():
    """A bare sqlite3.connect resets synchronous to FULL and waits only 5 s."""
    offenders = []
    for path in PIPELINE.glob("*.py"):
        if path.name == "audit_writer.py":
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"sqlite3\.connect\((AUDIT_DB|args\.audit_db|audit_path)\b", line):
                offenders.append(f"{path.name}:{line_no}")
    assert offenders == []
