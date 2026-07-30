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
