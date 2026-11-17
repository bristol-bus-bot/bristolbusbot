import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

import run_recorded_job


def invoke(tmp_path, monkeypatch, exit_code: int, *, skip: bool = False) -> dict:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], exit_code),
    )
    arguments = [
        "bbb-run-recorded-job", "--name", "timetable-shadow",
        "--state-dir", str(tmp_path),
    ]
    if skip:
        arguments.extend(("--skip-exit-code", "75"))
    arguments.extend(("--", "example-command"))
    monkeypatch.setattr(sys, "argv", arguments)
    run_recorded_job.main()
    return json.loads(
        (tmp_path / "timetable-shadow.json").read_text(encoding="utf-8"))


def test_lock_timeout_is_a_named_failure_not_a_skip(tmp_path, monkeypatch):
    state = invoke(tmp_path, monkeypatch, 73, skip=True)
    assert state["last_result"] == "failure"
    assert state["exit_code"] == 73
    assert state["failure_code"] == "lock_timeout"


def test_application_skip_remains_benign_and_clears_old_failure(
        tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, 73, skip=True)
    state = invoke(tmp_path, monkeypatch, 75, skip=True)
    assert state["last_result"] == "skipped"
    assert state["exit_code"] == 75
    assert "failure_code" not in state


def test_a_job_can_name_its_failure_and_failures_are_counted(tmp_path, monkeypatch):
    """#141: the daily message needs the reason and how many runs in a row."""
    import os

    def failing(*_args, **_kwargs):
        Path(os.environ["BBB_JOB_REASON_FILE"]).write_text("git_bundle_timed_out\n", encoding="utf-8")
        return subprocess.CompletedProcess([], 1)

    monkeypatch.setattr(subprocess, "run", failing)
    monkeypatch.setattr(sys, "argv", ["bbb-run-recorded-job", "--name", "backup",
                                      "--state-dir", str(tmp_path), "--", "x"])
    run_recorded_job.main()
    run_recorded_job.main()
    state = json.loads((tmp_path / "backup.json").read_text(encoding="utf-8"))
    assert state["failure_reason"] == "git_bundle_timed_out"
    assert state["consecutive_failures"] == 2
    assert not (tmp_path / "backup.reason").exists()

    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess([], 0))
    run_recorded_job.main()
    state = json.loads((tmp_path / "backup.json").read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 0 and "failure_reason" not in state


def test_free_text_never_becomes_a_reason(tmp_path):
    path = tmp_path / "x.reason"
    path.write_text("Traceback (most recent call last): File run.py, line 3", encoding="utf-8")
    assert run_recorded_job.read_reason(path) is None
    path.write_text("disk_full\n", encoding="utf-8")
    assert run_recorded_job.read_reason(path) == "disk_full"
