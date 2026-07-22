from datetime import date
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import build_timetable as builder
from build_timetable import (
    EXPECTED_FBRI,
    finalize_static_database,
    main,
    promote_atomically,
    validate,
)


def timetable(path: Path, end_date: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
        CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
        CREATE TABLE calendar (service_id TEXT, end_date TEXT);
        CREATE TABLE calendar_dates (service_id TEXT, date TEXT, exception_type INT);
    """)
    conn.execute("INSERT INTO agency VALUES ('A', 'FBRI')")
    conn.executemany(
        "INSERT INTO routes VALUES (?, 'A', ?)",
        [(f"R{index}", route) for index, route in enumerate(EXPECTED_FBRI)])
    conn.execute("INSERT INTO calendar VALUES ('WK', ?)", (end_date,))
    conn.commit()
    conn.close()


def test_validation_checks_integrity_routes_and_freshness(tmp_path):
    path = tmp_path / "timetable.db"
    timetable(path, "20260731")
    result = validate(path, today=date(2026, 7, 14))
    assert result["integrity"] == "ok"
    assert result["missing"] == []
    assert result["stale"] is False


def test_validation_rejects_expired_service_window(tmp_path):
    path = tmp_path / "timetable.db"
    timetable(path, "20260713")
    assert validate(path, today=date(2026, 7, 14))["stale"] is True


def test_atomic_promotion_keeps_previous_database(tmp_path):
    live = tmp_path / "timetable.db"
    staged = tmp_path / ".timetable.db.new"
    live.write_bytes(b"old")
    staged.write_bytes(b"new")
    previous = promote_atomically(staged, live)
    assert live.read_bytes() == b"new"
    assert previous.read_bytes() == b"old"
    assert not staged.exists()


def test_static_database_is_checkpointed_without_writable_sidecars(tmp_path):
    path = tmp_path / "timetable.db"
    timetable(path, "20260731")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO calendar VALUES ('EXTRA', '20260731')")
    conn.commit()
    conn.close()
    finalize_static_database(path)
    check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert check.execute("SELECT COUNT(*) FROM calendar").fetchone()[0] == 2
    check.close()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_direct_entry_refuses_before_starting_a_build(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build_timetable.py"])
    monkeypatch.setattr(
        "build_timetable.run",
        lambda *_: (_ for _ in ()).throw(AssertionError("build started")),
    )
    assert main() == 2


def configure_shadow_paths(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    gtfs = scratch / "busaudit_gtfs"
    first = scratch / "busaudit_first_txc"
    gtfs.mkdir(parents=True)
    first.mkdir()
    (gtfs / "shapes.txt").write_text("fixture\n", encoding="utf-8")
    (first / "first.zip").write_bytes(b"fixture")
    monkeypatch.setattr(builder, "TMP", scratch)
    monkeypatch.setattr(builder, "GTFS_DIR", gtfs)
    monkeypatch.setattr(builder, "WECA_DB", scratch / "weca.db")
    monkeypatch.setattr(
        builder, "SOURCE_STATUS", scratch / "source-status.json")
    monkeypatch.setattr(
        builder, "TIMETABLE_DB", tmp_path / "output" / "timetable.db")
    monkeypatch.setattr(builder, "BUSBOT_DB", tmp_path / "missing-fallback.db")
    return scratch


def test_complete_primary_sources_skip_tnds_fallback(tmp_path, monkeypatch):
    scratch = configure_shadow_paths(tmp_path, monkeypatch)
    commands = []

    def fake_run(command):
        commands.append([str(part) for part in command])
        if any("build_timetable_weca.py" in str(part) for part in command):
            builder.WECA_DB.write_bytes(b"candidate")
        return True

    complete = {
        "integrity": "ok", "missing": [], "stale": False,
        "fbri_count": 121, "latest_service": "20991231",
    }
    monkeypatch.setattr(builder, "run", fake_run)
    monkeypatch.setattr(builder, "validate", lambda _path: complete)
    monkeypatch.setattr(builder, "finalize_static_database", lambda _path: None)
    monkeypatch.setattr(
        builder.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        sys, "argv", ["build_timetable.py", "--skip-deploy", "--no-download"])

    assert builder.main() == 0
    assert not any(
        "audit_fetch_tnds.py" in " ".join(command) for command in commands)
    assert not any(
        "busaudit_tnds" in " ".join(command) for command in commands)
    status = json.loads(builder.SOURCE_STATUS.read_text(encoding="utf-8"))
    assert status["tnds"] == {
        "status": "not_needed", "missing_before_fallback": []}
    assert not (scratch / "busaudit_tnds").exists()


def test_missing_primary_route_requires_tnds_fallback(tmp_path, monkeypatch):
    configure_shadow_paths(tmp_path, monkeypatch)

    def fake_run(command):
        if any("build_timetable_weca.py" in str(part) for part in command):
            builder.WECA_DB.write_bytes(b"candidate")
        return True

    incomplete = {
        "integrity": "ok", "missing": ["42"], "stale": False,
        "fbri_count": 120, "latest_service": "20991231",
    }
    monkeypatch.setattr(builder, "run", fake_run)
    monkeypatch.setattr(builder, "validate", lambda _path: incomplete)
    monkeypatch.setattr(
        sys, "argv", ["build_timetable.py", "--skip-deploy", "--no-download"])

    assert builder.main() == 1
    assert not builder.SOURCE_STATUS.exists()
