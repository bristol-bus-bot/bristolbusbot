import gzip
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parent
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(REPO / "deploy"))

import daily_export as d  # noqa: E402
import get_daily_exports as pc  # noqa: E402

NOW = datetime(2026, 11, 20, 7, 45, tzinfo=timezone.utc)      # yesterday = 19 Nov
REPORTS = {
    "collector-anomaly/latest.json": b'{\r\n  "detectors": {}\r\n}\r\n',
    "data-health.json": b'{ "summary": {} }\n',
}


def make_audit(path: Path, days=("20261118", "20261119"), index=True) -> None:
    connection = sqlite3.connect(path)
    for table in d.READING_TABLES:
        connection.execute(f'CREATE TABLE "{table.name}" ({", ".join(table.columns)})')
        if table.by_poll_time:
            connection.execute(f'CREATE UNIQUE INDEX "pk_{table.name}" ON "{table.name}"(poll_at)')
        elif index or table.name != "timepoint_observations":
            connection.execute(f'CREATE INDEX "ix_{table.name}" ON "{table.name}"(service_date)')
    for day in days:
        for n in range(3):
            connection.execute(
                "INSERT INTO timepoint_observations (service_date, operator, route, trip_id, "
                "stop_sequence, observed_delay_s, vehicle_ref) VALUES (?, 'FBRI', '72', ?, ?, 60, '37001')",
                (day, f"T{n}", n + 1))
        connection.execute("INSERT INTO expected_trips (service_date, trip_id) VALUES (?, 'T0')", (day,))
        connection.execute("INSERT INTO daily_trip_coverage_days (service_date, is_valid) VALUES (?, 1)", (day,))
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        connection.execute("INSERT INTO poll_log (poll_at, ok) VALUES (?, 1)", (f"{iso}T08:00:00+00:00",))
    connection.commit()
    connection.close()


def make_app_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE rare_working_shadow_seen (event_id TEXT PRIMARY KEY, "
                       "observed_at TEXT NOT NULL, payload_json TEXT NOT NULL)")
    connection.executemany("INSERT INTO rare_working_shadow_seen VALUES (?, ?, ?)", [
        ("e1", "2026-11-19T10:00:00.000Z", json.dumps({"route": "72", "vehicle_ref": "37001"})),
        ("e2", "2026-11-18T23:30:00.000Z", json.dumps({"route": "4"})),    # 18 Nov in London
        ("e3", "2026-11-20T00:10:00.000Z", json.dumps({"route": "1"})),    # today, not yet
    ])
    connection.commit()
    connection.close()


def write_drafts(path: Path, count: int, start: int = 0) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for n in range(start, start + count):
            handle.write(json.dumps({"at": f"2026-11-19T10:{n:02d}:00Z", "draft": f"post {n}"}) + "\n")


def read_lines(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


@pytest.fixture
def sources(tmp_path):
    audit, app, training = tmp_path / "audit.db", tmp_path / "app.db", tmp_path / "drafts.jsonl"
    make_audit(audit)
    make_app_db(app)
    write_drafts(training, 3)
    for relative, content in REPORTS.items():
        report = tmp_path / "monitoring" / relative
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(content)
    return audit, app, training


def run(tmp_path, sources, store=None, now=NOW):
    audit, app, training = sources
    return d.run(audit_db=audit, app_db=app, training=training, state_dir=tmp_path / "state",
                 status_path=tmp_path / "monitoring" / "daily-export.json", store=store, now=now,
                 monitoring_dir=tmp_path / "monitoring")


class FakeStore:
    def __init__(self, fail_parts=()):
        self.pushed, self.fail_parts, self.deleted = {}, set(fail_parts), []

    def push(self, branch, path_in_repo, file, message):
        self.push_files(branch, {path_in_repo: file}, message)

    def push_files(self, branch, files, message):
        if branch.split("/")[0] in self.fail_parts:
            raise d.PartError("push_failed")
        self.pushed[branch] = {name: file.read_bytes() for name, file in files.items()}

    def branches(self):
        return list(self.pushed) + ["readings/2026-07-01", "reports/2026-07-01T0745Z", "not-ours"]

    def delete(self, branches):
        self.deleted += branches


def test_readings_day_file_holds_only_that_day_through_indexes(tmp_path, sources):
    file, counts = d.export_readings(sources[0], date(2026, 11, 19), tmp_path)
    plain = tmp_path / "day.sqlite"
    plain.write_bytes(gzip.decompress(file.read_bytes()))
    connection = sqlite3.connect(plain)
    assert connection.execute("SELECT DISTINCT service_date FROM timepoint_observations").fetchall() == [("20261119",)]
    assert counts["rows"] == 3 and counts["tables"]["poll_log"] == 1
    info = dict(connection.execute("SELECT key, value FROM export_info"))
    assert info["service_date"] == "20261119"
    connection.close()


def test_readings_refuse_a_query_that_would_scan_a_whole_table(tmp_path):
    audit = tmp_path / "audit.db"
    make_audit(audit, index=False)
    with pytest.raises(d.PartError) as caught:
        d.export_readings(audit, date(2026, 11, 19), tmp_path)
    assert caught.value.code == "query_would_scan"


def test_rare_workings_are_selected_by_london_day(tmp_path, sources):
    file, counts = d.export_rare_workings(sources[1], date(2026, 11, 19), tmp_path)
    assert [row["event_id"] for row in read_lines(file)] == ["e1"]
    file, _ = d.export_rare_workings(sources[1], date(2026, 11, 18), tmp_path)
    assert [row["event_id"] for row in read_lines(file)] == ["e2"]


def test_writer_drafts_follow_the_offset_across_a_rotation(tmp_path):
    training = tmp_path / "drafts.jsonl"
    write_drafts(training, 3)
    lines, cursor, notes = d.read_new_draft_lines(training, None)
    assert len(lines) == 3 and notes.get("first_run")
    write_drafts(training, 2, start=3)
    with training.open("a", encoding="utf-8") as handle:
        handle.write('{"partial": ')               # an incomplete line waits
    lines, cursor, _ = d.read_new_draft_lines(training, cursor)
    assert [json.loads(x)["draft"] for x in lines] == ["post 3", "post 4"]
    with training.open("a", encoding="utf-8") as handle:
        handle.write('true}\n')
    os.replace(training, Path(f"{training}.1"))     # the bot rotates at 25 MB
    write_drafts(training, 1, start=10)
    lines, cursor, notes = d.read_new_draft_lines(training, cursor)
    assert [json.loads(x).get("draft", "partial") for x in lines] == ["partial", "post 10"]
    assert "lines_may_be_missing" not in notes
    lines, _, _ = d.read_new_draft_lines(training, cursor)
    assert lines == []


def test_secret_scan_stops_a_part(tmp_path):
    training = tmp_path / "drafts.jsonl"
    training.write_text(json.dumps({"brief": "key AIza" + "x" * 35}) + "\n", encoding="utf-8")
    with pytest.raises(d.PartError) as caught:
        d.export_writer_drafts(training, None, tmp_path, "label")
    assert caught.value.code == "secret_scan_finding"


def test_catch_up_takes_yesterday_first_then_the_oldest_missing_days():
    days = d.days_to_export({"2026-11-15"}, date(2026, 11, 14), date(2026, 11, 19), 4)
    assert [x.isoformat() for x in days] == ["2026-11-19", "2026-11-14", "2026-11-16", "2026-11-17"]


def test_nightly_run_catches_up_and_each_part_stands_alone(tmp_path, sources):
    store = FakeStore(fail_parts={"rare-workings"})
    status = run(tmp_path, sources, store)
    readings = status["parts"]["readings"]
    assert readings["ok"] and readings["days"][0] == "2026-11-19"
    assert len(readings["days"]) == 4 and readings["days_left"] == 2
    assert status["parts"]["rare-workings"] == {**status["parts"]["rare-workings"],
                                                "ok": False, "reason": "push_failed"}
    assert status["parts"]["writer-drafts"]["ok"] and status["parts"]["writer-drafts"]["rows"] == 3
    assert status["retention"]["deleted"] == 2
    assert store.deleted == ["readings/2026-07-01", "reports/2026-07-01T0745Z"]
    saved = json.loads((tmp_path / "monitoring" / "daily-export.json").read_text())
    assert saved["parts"]["rare-workings"]["reason"] == "push_failed"

    # Next night: the missed rare-working days and the rest of the gap go out;
    # writer drafts send only what is new.
    store.fail_parts.clear()
    write_drafts(sources[2], 1, start=50)
    status = run(tmp_path, sources, store, now=NOW.replace(day=21))
    assert status["parts"]["readings"]["days"][0] == "2026-11-20"
    assert "2026-11-19" in status["parts"]["rare-workings"]["days"]
    assert status["parts"]["writer-drafts"]["rows"] == 1


def test_a_failed_draft_push_does_not_advance_the_offset(tmp_path, sources):
    run(tmp_path, sources, FakeStore(fail_parts={"writer-drafts"}))
    status = run(tmp_path, sources, FakeStore())
    assert status["parts"]["writer-drafts"]["rows"] == 3


@pytest.mark.parametrize("reason", ["source_missing", "secret_scan_finding", "push_failed"])
def test_reports_fail_as_a_pair_and_leave_other_parts_running(tmp_path, sources, reason):
    report = tmp_path / "monitoring" / "data-health.json"
    store = FakeStore(fail_parts={"reports"} if reason == "push_failed" else ())
    if reason == "source_missing":
        report.unlink()
    if reason == "secret_scan_finding":
        report.write_text(json.dumps({"detail": "AIza" + "x" * 35}), encoding="utf-8")

    parts = run(tmp_path, sources, store)["parts"]
    assert parts.pop("reports") == {"ok": False, "reason": reason}
    assert all(part["ok"] for part in parts.values())
    assert not any(branch.startswith("reports/") for branch in store.pushed)
    assert not list((tmp_path / "state" / "outgoing").glob("reports-*"))


def test_unconfigured_store_writes_a_status_and_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("BBB_DAILY_EXPORT_REMOTE", raising=False)
    status = tmp_path / "status.json"
    assert d.main(["--status", str(status), "--state-dir", str(tmp_path / "s")]) == 1
    parts = json.loads(status.read_text())["parts"]
    assert {p["reason"] for p in parts.values()} == {"store_not_configured"}


def test_end_to_end_through_a_git_store_to_the_pc(tmp_path, sources):
    remote = tmp_path / "store.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    store = d.GitStore(str(remote), None)
    status = run(tmp_path, sources, store)
    assert all(part["ok"] for part in status["parts"].values()), status
    branches = store.branches()
    assert "readings/2026-11-19" in branches and "rare-workings/2026-11-19" in branches

    root = tmp_path / "pc"
    saved = pc.download(str(remote), root)
    assert (root / "readings" / "2026-11-19.sqlite.gz") in saved
    reports = root / "reports" / "2026-11-20T0745Z"
    assert (reports / "collector-anomaly.json").read_bytes() == REPORTS["collector-anomaly/latest.json"]
    assert (reports / "data-health.json").read_bytes() == REPORTS["data-health.json"]
    assert pc.download(str(remote), root) == []          # nothing new the second time
    (reports / "data-health.json").unlink()
    assert reports / "data-health.json" in pc.download(str(remote), root)
    combined = sqlite3.connect(pc.combine(root))
    # The first night sends 19 Nov and the three oldest gap days (14-16 Nov).
    assert combined.execute("SELECT COUNT(*) FROM timepoint_observations").fetchone()[0] == 3
    assert combined.execute("SELECT COUNT(*) FROM export_days").fetchone()[0] == 4
    combined.close()
    drafts = next((root / "writer-drafts").glob("*.jsonl.gz"))
    assert len(read_lines(drafts)) == 3

    store.delete(["readings/2026-11-18"])
    assert "readings/2026-11-18" not in store.branches()
