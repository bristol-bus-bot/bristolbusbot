#!/usr/bin/env python3
"""Run one timer command and persist its last start/success/failure state."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STATE = Path("/var/lib/bristolbusbot/monitoring/jobs")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
LOCK_TIMEOUT_EXIT_CODE = 73


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o640)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(temporary_path, 0, path.parent.stat().st_gid)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


REASON_PATTERN = re.compile(r"^[a-z][a-z_]{0,39}$")


def read_reason(path: Path) -> str | None:
    """The job's reason code, if it wrote a well-formed one."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if REASON_PATTERN.fullmatch(text) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--skip-exit-code", type=int, action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not NAME_RE.fullmatch(args.name):
        raise SystemExit("unsafe job name")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("missing command after --")
    path = args.state_dir / f"{args.name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"name": args.name}
    started = time.monotonic()
    # A job may write one short reason code here when it fails (#141), for
    # example "git_bundle_timed_out". Never free text: it reaches Slack.
    reason_file = args.state_dir / f"{args.name}.reason"
    reason_file.unlink(missing_ok=True)
    os.environ["BBB_JOB_REASON_FILE"] = str(reason_file)
    payload.update({"name": args.name, "last_started_at": now(),
                    "last_result": "running", "command": command[0]})
    write_state(path, payload)
    try:
        result = subprocess.run(command, check=False)
        code = result.returncode
    except OSError as exc:
        code = 127
        payload["last_error"] = str(exc)
    payload["last_finished_at"] = now()
    payload["duration_seconds"] = round(time.monotonic() - started, 3)
    payload["exit_code"] = code
    if code == 0:
        payload["last_result"] = "success"
        payload["last_success_at"] = payload["last_finished_at"]
        payload.pop("failure_code", None)
        payload.pop("failure_reason", None)
        payload["consecutive_failures"] = 0
    elif code in args.skip_exit_code:
        payload["last_result"] = "skipped"
        payload["last_skipped_at"] = payload["last_finished_at"]
        payload.pop("failure_code", None)
        code = 0
    else:
        payload["last_result"] = "failure"
        payload["last_failure_at"] = payload["last_finished_at"]
        payload["failure_code"] = (
            "lock_timeout" if code == LOCK_TIMEOUT_EXIT_CODE
            else "command_failed")
        payload["consecutive_failures"] = int(payload.get("consecutive_failures") or 0) + 1
        reason = read_reason(reason_file)
        if reason:
            payload["failure_reason"] = reason
        else:
            payload.pop("failure_reason", None)
    reason_file.unlink(missing_ok=True)
    write_state(path, payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
