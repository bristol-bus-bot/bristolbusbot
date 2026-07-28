import sqlite3
import sys
import threading
import time
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_export  # noqa: E402
import audit_rollup  # noqa: E402


def test_rollup_returns_failure_when_audit_database_is_missing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(audit_rollup, "AUDIT_DB", str(tmp_path / "missing.db"))

    assert audit_rollup.main() == 1


def test_rollup_waits_for_a_short_collector_write(tmp_path):
    database = tmp_path / "audit.db"
    setup = sqlite3.connect(database)
    setup.execute("CREATE TABLE sample (value INTEGER)")
    setup.commit()
    setup.close()

    collector = sqlite3.connect(database, check_same_thread=False)
    collector.execute("BEGIN IMMEDIATE")
    collector.execute("INSERT INTO sample VALUES (1)")

    def finish_collector_write():
        time.sleep(0.1)
        collector.commit()
        collector.close()

    release = threading.Thread(target=finish_collector_write)
    release.start()
    rollup = audit_rollup.connect_audit_db(database)
    try:
        rollup.execute("INSERT INTO sample VALUES (2)")
        rollup.commit()
        assert rollup.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 2
        assert rollup.execute("PRAGMA busy_timeout").fetchone()[0] == 60_000
    finally:
        rollup.close()
        release.join(timeout=2)


def test_export_returns_failure_when_audit_database_is_missing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(audit_export, "AUDIT_DB", str(tmp_path / "missing.db"))

    assert audit_export.main() == 1
