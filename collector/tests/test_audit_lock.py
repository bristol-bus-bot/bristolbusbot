"""A busy audit.db must not stop the collector or the live map (#94, #96).

These use a real file-backed audit.db and a second connection that holds the
write lock, the way the 07:00 audit rollup does.
"""
import sqlite3
import time

import pytest

from collector import audit_db, live_db
from collector.run import AuditBacklog, _write_audit, vm_cycle
from test_run import LDN, NOW, VM_FEED, BristolBoxBoundary, setup

SHORT_WAIT_S = 0.3


@pytest.fixture
def file_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_db, "BUSY_TIMEOUT_S", SHORT_WAIT_S)
    path = tmp_path / "audit.db"
    conn = audit_db.connect(str(path))
    yield path, conn
    conn.close()


def _hold_lock(path):
    other = sqlite3.connect(path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    return other


def test_collector_audit_connection_waits_and_syncs_normally(file_audit):
    _, conn = file_audit
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert audit_db.BUSY_TIMEOUT_S == SHORT_WAIT_S
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(SHORT_WAIT_S * 1000)


def test_production_wait_fits_inside_the_watchdog():
    import importlib
    fresh = importlib.reload(audit_db)
    # fetch (5 s connect + 20 s read) + one busy wait + processing < 120 s
    assert fresh.BUSY_TIMEOUT_S <= 20


def test_locked_audit_db_still_commits_live_db_and_holds_readings(file_audit):
    path, audit_conn = file_audit
    tt, live_conn, _, boundary, cfg = setup()
    backlog = AuditBacklog()
    holder = _hold_lock(path)
    try:
        result = vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary,
                          cfg, LDN, now_utc=NOW, backlog=backlog)
    finally:
        holder.rollback()
        holder.close()

    assert result["ok"] and result["matched"] == 1
    assert result["audit_written"] is False
    # The live map was updated and committed despite the lock.
    assert not live_conn.in_transaction
    assert live_conn.execute("SELECT delay_seconds FROM vehicles").fetchone()[0] == 120
    # The audit connection was left with no open transaction.
    assert not audit_conn.in_transaction
    assert [item[0] for item in backlog.items] == ["obs", "poll"]
    assert audit_conn.execute("SELECT COUNT(*) FROM timepoint_observations").fetchone()[0] == 0

    # The next poll that gets the lock writes the held reading and poll record.
    later = vm_cycle(lambda: None, tt, live_conn, audit_conn, boundary, cfg,
                     LDN, now_utc=NOW.replace(minute=28), backlog=backlog)
    assert later["audit_written"] is True
    assert backlog.items == []
    obs = audit_conn.execute(
        "SELECT observed_delay_s FROM timepoint_observations").fetchall()
    assert obs == [(120,)]
    polls = audit_conn.execute(
        "SELECT poll_at, ok, obs_written FROM poll_log ORDER BY poll_at").fetchall()
    assert polls == [(NOW.isoformat(), 1, 1),
                     (NOW.replace(minute=28).isoformat(), 0, 0)]


def test_a_busy_poll_waits_once_however_many_writes_it_has(file_audit):
    path, audit_conn = file_audit
    backlog = AuditBacklog()
    obs = ("20260610", "FBRI", "75", "T", "J", 1, "S", "2026-06-10T11:25:00+01:00",
           60, 1, 10, "2026-06-10T10:26:00+00:00", "V", 0, "exact")
    ops = [("obs", obs)] * 20
    evidence = [{"vehicle_ref": f"V{i}"} for i in range(10)]
    counters = {"evidence_written": 0, "evidence_dropped": 0,
                "evidence_deduplicated": 0, "evidence_scope_dropped": 0,
                "evidence_quota_dropped": 0, "evidence_errors": 0}
    holder = _hold_lock(path)
    try:
        started = time.monotonic()
        written = _write_audit(audit_conn, backlog, ops, evidence,
                               NOW.isoformat(), True, counters, LDN)
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert written is False
    assert elapsed < SHORT_WAIT_S * 3
    # Evidence is a debugging sample: dropped and counted, never held.
    assert counters["evidence_errors"] == 10
    assert len(backlog.items) == 21  # 20 readings + the poll record


def test_backlog_is_capped_oldest_first(monkeypatch):
    monkeypatch.setattr(AuditBacklog, "MAX_ITEMS", 5)
    backlog = AuditBacklog()
    backlog.extend([("obs", n) for n in range(4)])
    backlog.extend([("obs", n) for n in range(4, 8)])
    assert [item[1] for item in backlog.items] == [3, 4, 5, 6, 7]
    assert backlog.dropped == 3


def test_a_non_lock_database_error_is_not_swallowed(file_audit):
    _, audit_conn = file_audit
    with pytest.raises(sqlite3.Error):
        _write_audit(audit_conn, AuditBacklog(), [("obs", ("too", "short"))],
                     [], NOW.isoformat(), True, {}, LDN)


def test_fetch_has_no_retries_and_a_split_timeout():
    from collector.config import Config
    from collector.run import CONNECT_TIMEOUT_S, _timeout, make_session
    adapter = make_session().get_adapter("https://data.bus-data.dft.gov.uk/")
    assert adapter.max_retries.total == 0
    connect, read = _timeout(Config(bods_api_key="test"))
    assert connect == CONNECT_TIMEOUT_S == 5
    assert read == 20
