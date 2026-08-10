#!/usr/bin/env python3
"""Reusable, fail-closed transaction for promoting one named data artifact.

This module intentionally has no command-line interface. A caller must provide
a code-defined contract, validator, consumer restart, and health check. The
production wrapper for each artifact can therefore expose a small allowlist
instead of accepting arbitrary source and destination paths.

The caller is also responsible for serialising production invocations with the
Pi's shared heavy-I/O lock. Keeping that policy in the systemd unit makes the
lock visible alongside backup, timetable, and deployment jobs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping


NO_CHANGE = 75
ARTIFACT_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class DataPromotionError(RuntimeError):
    """A candidate could not safely become the live data artifact."""


@dataclass(frozen=True)
class ArtifactContract:
    """Code-owned paths and limits for one explicitly supported artifact."""

    name: str
    live: Path
    candidate: Path
    previous: Path
    state: Path
    maximum_bytes: int
    mode: int = 0o640

    def check(self) -> None:
        if not ARTIFACT_RE.fullmatch(self.name):
            raise DataPromotionError("artifact name is invalid")
        if self.maximum_bytes <= 0:
            raise DataPromotionError("artifact size limit is invalid")
        paths = (self.live, self.candidate, self.previous, self.state)
        if len({str(path.resolve(strict=False)) for path in paths}) != len(paths):
            raise DataPromotionError("artifact paths must be distinct")
        for path in paths:
            _require_directory(path.parent, f"{path.name} parent")


Validator = Callable[[bytes], Mapping[str, object]]
Comparator = Callable[
    [Mapping[str, object], Mapping[str, object]], Mapping[str, object]]
Restarter = Callable[[], None]
HealthCheck = Callable[[str, Mapping[str, object]], bool]
FaultHook = Callable[[str], None]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_directory(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise DataPromotionError(f"{label} is absent") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise DataPromotionError(f"{label} is unsafe")
    return details


def _read_regular(path: Path, maximum: int, label: str) \
        -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        if path.is_symlink():
            raise OSError("symbolic links are not accepted")
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) \
                or details.st_size <= 0 or details.st_size > maximum:
            raise OSError("not a bounded regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(maximum + 1)
        if len(raw) != details.st_size or len(raw) > maximum:
            raise OSError("file changed while it was being read")
        return raw, details
    except OSError as exc:
        raise DataPromotionError(f"{label} is missing or unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validated(raw: bytes, validate: Validator, label: str) -> dict[str, object]:
    try:
        summary = dict(validate(raw))
        json.dumps(summary, allow_nan=False)
    except Exception as exc:
        raise DataPromotionError(f"{label} failed validation: {exc}") from exc
    actual_digest = _digest(raw)
    supplied_digest = summary.get("sha256")
    if supplied_digest is not None and supplied_digest != actual_digest:
        raise DataPromotionError(f"{label} validator reported the wrong digest")
    summary["sha256"] = actual_digest
    summary["bytes"] = len(raw)
    return summary


def _directory_fsync(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, raw: bytes, *, owner: os.stat_result,
                  mode: int) -> None:
    _require_directory(path.parent, f"{path.name} parent")
    temporary = path.with_name(
        f".{path.name}.new-{os.getpid()}-{time.monotonic_ns()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if hasattr(os, "chown"):
            os.chown(temporary, owner.st_uid, owner.st_gid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        _directory_fsync(path.parent)
    finally:
        if descriptor not in (-1, None):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _atomic_state(contract: ArtifactContract, record: dict[str, object],
                  owner: os.stat_result) -> None:
    raw = (json.dumps(
        record, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    _atomic_bytes(contract.state, raw, owner=owner, mode=contract.mode)


def _load_state(contract: ArtifactContract) -> dict[str, object] | None:
    if not contract.state.exists() and not contract.state.is_symlink():
        return None
    raw, _ = _read_regular(contract.state, 128 * 1024, "promotion state")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataPromotionError("promotion state is invalid JSON") from exc
    if not isinstance(value, dict):
        raise DataPromotionError("promotion state has the wrong shape")
    return value


def _finish(record: dict[str, object], outcome: str,
            started: float) -> None:
    record.update({
        "outcome": outcome,
        "finished_at": utcnow(),
        "duration_seconds": round(time.monotonic() - started, 3),
    })


def _remove_candidate(contract: ArtifactContract) -> None:
    contract.candidate.unlink(missing_ok=True)


def _record_digest(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, dict) or not isinstance(value.get("sha256"), str):
        raise DataPromotionError(
            f"unfinished promotion state has no valid {field} digest")
    return value["sha256"]


def _restore_previous(
    contract: ArtifactContract,
    *,
    previous_raw: bytes,
    previous_summary: Mapping[str, object],
    owner: os.stat_result,
    restart: Restarter,
    healthy: HealthCheck,
) -> bool:
    try:
        _atomic_bytes(
            contract.live, previous_raw, owner=owner, mode=contract.mode)
        restart()
        return bool(healthy(str(previous_summary["sha256"]), previous_summary))
    except Exception:
        return False


def _recover_running(
    contract: ArtifactContract,
    *,
    record: dict[str, object],
    candidate_summary: dict[str, object],
    live_summary: dict[str, object],
    live_info: os.stat_result,
    validate: Validator,
    restart: Restarter,
    healthy: HealthCheck,
    started: float,
) -> tuple[int, dict[str, object]] | None:
    recorded_candidate = _record_digest(record, "candidate")
    previous_digest = _record_digest(record, "previous")
    if record.get("artifact") != contract.name \
            or recorded_candidate != candidate_summary["sha256"]:
        raise DataPromotionError(
            "unfinished promotion state does not match this candidate")
    if live_summary["sha256"] == previous_digest:
        return None
    if live_summary["sha256"] != candidate_summary["sha256"]:
        raise DataPromotionError(
            "live data matches neither side of the unfinished promotion")

    previous_raw, _ = _read_regular(
        contract.previous, contract.maximum_bytes, "previous artifact")
    previous_summary = _validated(
        previous_raw, validate, "previous artifact")
    if previous_summary["sha256"] != previous_digest:
        raise DataPromotionError(
            "previous data does not match unfinished promotion state")

    try:
        restart()
        accepted = bool(healthy(
            str(candidate_summary["sha256"]), candidate_summary))
    except Exception:
        accepted = False
    if accepted:
        record["recovered_interrupted_transaction"] = True
        _finish(record, "accepted", started)
        _atomic_state(contract, record, live_info)
        _remove_candidate(contract)
        return 0, record

    recovery_healthy = _restore_previous(
        contract,
        previous_raw=previous_raw,
        previous_summary=previous_summary,
        owner=live_info,
        restart=restart,
        healthy=healthy,
    )
    record.update({
        "recovered_interrupted_transaction": True,
        "error": "interrupted candidate failed its health gate",
        "recovery_healthy": recovery_healthy,
    })
    _finish(
        record, "rolled_back" if recovery_healthy else "rollback_failed",
        started)
    _atomic_state(contract, record, live_info)
    _remove_candidate(contract)
    if recovery_healthy:
        raise DataPromotionError(
            "interrupted candidate was rolled back after its health gate failed")
    raise DataPromotionError(
        "interrupted promotion and rollback health gate both failed")


def promote(
    contract: ArtifactContract,
    *,
    validate: Validator,
    restart: Restarter,
    healthy: HealthCheck,
    compare: Comparator | None = None,
    context: Mapping[str, object] | None = None,
    fault: FaultHook | None = None,
) -> tuple[int, dict[str, object]]:
    """Validate, atomically promote, health-gate, and if needed roll back."""
    contract.check()
    started = time.monotonic()
    candidate_raw, _ = _read_regular(
        contract.candidate, contract.maximum_bytes, "candidate artifact")
    candidate_summary = _validated(
        candidate_raw, validate, "candidate artifact")
    live_raw, live_info = _read_regular(
        contract.live, contract.maximum_bytes, "live artifact")
    live_summary = _validated(live_raw, validate, "live artifact")
    comparison: dict[str, object] | None = None
    if compare:
        try:
            comparison = dict(compare(candidate_summary, live_summary))
            json.dumps(comparison, allow_nan=False)
        except Exception as exc:
            raise DataPromotionError(
                f"candidate comparison failed: {exc}") from exc

    prior_state = _load_state(contract)
    if prior_state and prior_state.get("outcome") == "running":
        recovered = _recover_running(
            contract,
            record=prior_state,
            candidate_summary=candidate_summary,
            live_summary=live_summary,
            live_info=live_info,
            validate=validate,
            restart=restart,
            healthy=healthy,
            started=started,
        )
        if recovered is not None:
            return recovered

    record: dict[str, object] = {
        "schema_version": 1,
        "artifact": contract.name,
        "started_at": utcnow(),
        "outcome": "running",
        "candidate": candidate_summary,
        "previous": live_summary,
    }
    if comparison is not None:
        record["comparison"] = comparison
    if context:
        try:
            json.dumps(context, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DataPromotionError("promotion context is not JSON-safe") from exc
        record["context"] = dict(context)
    _atomic_state(contract, record, live_info)

    if live_summary["sha256"] == candidate_summary["sha256"]:
        _finish(record, "no_change", started)
        _atomic_state(contract, record, live_info)
        _remove_candidate(contract)
        return NO_CHANGE, record

    changed = False
    try:
        _atomic_bytes(
            contract.previous, live_raw, owner=live_info, mode=contract.mode)
        if fault:
            fault("after_previous")
        # From this point onward, conservatively run rollback even when the
        # replace helper itself reports a late fsync error.
        changed = True
        _atomic_bytes(
            contract.live, candidate_raw, owner=live_info, mode=contract.mode)
        if fault:
            fault("after_replace")
        restart()
        if fault:
            fault("before_health")
        if not healthy(str(candidate_summary["sha256"]), candidate_summary):
            raise DataPromotionError(
                "consumer did not report the promoted data digest")
    except Exception as exc:
        if not changed:
            record["error"] = str(exc)[:500]
            _finish(record, "failed_before_replace", started)
            _atomic_state(contract, record, live_info)
            raise DataPromotionError(
                "promotion failed before live data was replaced") from exc
        recovery_healthy = _restore_previous(
            contract,
            previous_raw=live_raw,
            previous_summary=live_summary,
            owner=live_info,
            restart=restart,
            healthy=healthy,
        )
        record.update({
            "error": str(exc)[:500],
            "recovery_healthy": recovery_healthy,
        })
        _finish(
            record, "rolled_back" if recovery_healthy else "rollback_failed",
            started)
        _atomic_state(contract, record, live_info)
        _remove_candidate(contract)
        if recovery_healthy:
            raise DataPromotionError(
                "candidate was rolled back after its health gate failed") from exc
        raise DataPromotionError(
            "promotion and rollback health gate both failed") from exc

    _finish(record, "accepted", started)
    _atomic_state(contract, record, live_info)
    _remove_candidate(contract)
    return 0, record
