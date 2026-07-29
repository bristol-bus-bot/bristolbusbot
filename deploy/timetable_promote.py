#!/usr/bin/env python3
"""Promote one fixed validated timetable candidate with automatic rollback."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from timetable_control import paths, promote, rollback, validate
from timetable_delivery import BRISTOL_TZ, compare_with_current, sha256_file
from timetable_manifest import verify_manifest


SHADOW_ROOT = Path("/var/lib/bristolbusbot/timetable-shadow")
LIVE_ROOT = Path("/var/lib/bristolbusbot/pipeline")
MONITORING_ROOT = Path("/var/lib/bristolbusbot/monitoring")
ENABLE_MARKER = Path("/etc/bristolbusbot/timetable-promotion-enabled")
EXPECTED_OWNER = "@BBB_DEPLOY_USER@"
MINIMUM_SERVICE_DAYS = 14
COPY_CHUNK = 1024 * 1024
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
COLLECTOR_VERIFY_TIMEOUT_SECONDS = 45
COLLECTOR_VERIFY_ATTEMPTS = 6
HEALTH_USER_AGENT = "bristolbusbot-timetable-promoter/1"
MINIMUM_SEARCH_STOPS = 1000
SITE_FUNCTIONAL_TIMEOUT_SECONDS = 20


class PromotionError(RuntimeError):
    """A fixed promotion safety gate failed."""

    def __init__(self, code: str, message: str,
                 safe_context: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.safe_context = dict(safe_context or {})


class PromotionSkipped(PromotionError):
    """An idempotent or disabled promotion did not need to run."""


@dataclass(frozen=True)
class PromotionConfig:
    shadow_root: Path = SHADOW_ROOT
    live_root: Path = LIVE_ROOT
    monitoring_root: Path = MONITORING_ROOT
    enable_marker: Path = ENABLE_MARKER
    expected_owner: str = EXPECTED_OWNER
    expected_uid: int | None = None
    expected_gid: int | None = None
    minimum_service_days: int = MINIMUM_SERVICE_DAYS

    @property
    def candidate_root(self) -> Path:
        return self.shadow_root / "candidate"

    @property
    def delivery_state(self) -> Path:
        return self.shadow_root / "state.json"

    @property
    def promotion_state(self) -> Path:
        return self.monitoring_root / "timetable-promotion.json"


@dataclass(frozen=True)
class Candidate:
    run_id: str
    commit: str
    sha256: str
    validation: dict[str, object]
    comparison: dict[str, object]
    tnds_status: str


ATTENDED_RE = re.compile(r"([1-9][0-9]*)-([0-9a-f]{64})")


@dataclass(frozen=True)
class PromotionRequest:
    mode: str
    run_id: str | None = None
    sha256: str | None = None


def promotion_request(value: str) -> PromotionRequest:
    if value == "auto":
        return PromotionRequest("auto")
    matched = ATTENDED_RE.fullmatch(value)
    if not matched:
        raise argparse.ArgumentTypeError(
            "candidate must be 'auto' or RUN_ID-SHA256")
    return PromotionRequest("attended", matched.group(1), matched.group(2))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def owner_ids(config: PromotionConfig) -> tuple[int, int]:
    if config.expected_uid is not None and config.expected_gid is not None:
        return config.expected_uid, config.expected_gid
    try:
        import pwd  # pylint: disable=import-outside-toplevel
        owner = pwd.getpwnam(config.expected_owner)
    except (ImportError, KeyError) as exc:
        raise PromotionError("owner_missing", "expected timetable owner does not exist") from exc
    return owner.pw_uid, owner.pw_gid


def require_regular(path: Path, *, uid: int | None = None,
                    mode: int | None = None) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PromotionError("unsafe_path", f"required file is unavailable: {path.name}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise PromotionError("unsafe_path", f"required file is not regular: {path.name}")
    if uid is not None and details.st_uid != uid:
        raise PromotionError("unsafe_owner", f"unexpected owner for {path.name}")
    if mode is not None and os.name != "nt" \
            and stat.S_IMODE(details.st_mode) != mode:
        raise PromotionError("unsafe_mode", f"unexpected mode for {path.name}")
    return details


def require_directory(path: Path, *, uid: int | None = None,
                      mode: int | None = None) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PromotionError("unsafe_path", f"required directory is unavailable: {path.name}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise PromotionError("unsafe_path", f"required path is not a directory: {path.name}")
    if uid is not None and details.st_uid != uid:
        raise PromotionError("unsafe_owner", f"unexpected owner for {path.name}")
    if mode is not None and os.name != "nt" \
            and stat.S_IMODE(details.st_mode) != mode:
        raise PromotionError("unsafe_mode", f"unexpected mode for {path.name}")
    return details


def read_json(path: Path) -> dict[str, object]:
    details = require_regular(path)
    if details.st_size <= 0 or details.st_size > 2 * 1024 * 1024:
        raise PromotionError("invalid_state", f"unsafe JSON size for {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError("invalid_state", f"unreadable JSON in {path.name}") from exc
    if not isinstance(value, dict):
        raise PromotionError("invalid_state", f"JSON root is not an object in {path.name}")
    return value


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(temporary, 0, path.parent.stat().st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SystemServices:
    """Fixed service restarts and health checks used by the root transaction."""

    def restart(self, component: str) -> None:
        if component not in {"collector", "site", "bot"}:
            raise PromotionError("unsafe_service", "refusing unknown timetable consumer")
        subprocess.run(
            ["/usr/local/sbin/bbb-deploy-control", "restart", component],
            check=True, timeout=90)

    @staticmethod
    def _json(url: str, *, timeout: int = 10) -> dict[str, object]:
        request = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": HEALTH_USER_AGENT,
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("health response is not an object")
        return value

    def wait_component(self, component: str) -> bool:
        attempts, delay = ((COLLECTOR_VERIFY_ATTEMPTS, 5)
                           if component == "collector" else (30, 2))
        for _ in range(attempts):
            try:
                if component == "collector":
                    result = subprocess.run(
                        ["/usr/local/libexec/bbb-verify-collector-state",
                         "--max-poll-age", "180"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, timeout=COLLECTOR_VERIFY_TIMEOUT_SECONDS)
                    if result.returncode == 0:
                        return True
                elif component == "site":
                    healthy = self._json(
                        "http://127.0.0.1:5002/healthz").get("status") in {
                            "ok", "warn"}
                    search = self._json(
                        "http://127.0.0.1:5002/api/stops-with-locality",
                        timeout=SITE_FUNCTIONAL_TIMEOUT_SECONDS,
                    )
                    stops = search.get("stops")
                    if healthy and isinstance(stops, list) \
                            and len(stops) >= MINIMUM_SEARCH_STOPS:
                        return True
                else:
                    value = self._json("http://127.0.0.1:3010/api/health")
                    details = value.get("details")
                    health = details.get("healthData") \
                        if isinstance(details, dict) else None
                    database = health.get("database") \
                        if isinstance(health, dict) else None
                    timetable = database.get("timetable") \
                        if isinstance(database, dict) else None
                    if value.get("success") is True \
                            and value.get("runtime") == "systemd" \
                            and isinstance(timetable, dict) \
                            and timetable.get("connected") is True:
                        return True
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                pass
            time.sleep(delay)
        return False

    def wait_public(self) -> bool:
        for _ in range(15):
            try:
                if self._json("https://bristolbuses.live/healthz").get("status") == "ok":
                    return True
            except (OSError, RuntimeError, ValueError):
                pass
            time.sleep(2)
        return False


class TimetablePromoter:
    def __init__(self, config: PromotionConfig, services: SystemServices, *,
                 now: Callable[[], datetime] = utcnow,
                 fault: Callable[[str], None] | None = None):
        self.config = config
        self.services = services
        self.now = now
        self.fault = fault or (lambda _stage: None)
        self.uid, self.gid = owner_ids(config)

    def state_document(self) -> dict[str, object]:
        path = self.config.promotion_state
        if not path.exists() and not path.is_symlink():
            return {}
        return read_json(path)

    @staticmethod
    def _last_accepted(document: dict[str, object]) -> dict[str, object] | None:
        accepted = document.get("last_accepted")
        if isinstance(accepted, dict):
            return accepted
        run_id = document.get("last_accepted_run_id")
        accepted_at = document.get("last_accepted_at")
        sha256 = document.get("database_sha256")
        if run_id and accepted_at and sha256:
            return {
                "run_id": run_id,
                "accepted_at": accepted_at,
                "database_sha256": sha256,
                "commit": document.get("commit"),
            }
        return None

    def write_state(self, attempt: dict[str, object]) -> dict[str, object]:
        previous = self.state_document()
        accepted = self._last_accepted(previous)
        if attempt.get("outcome") == "accepted":
            accepted = {
                "run_id": attempt.get("run_id"),
                "accepted_at": attempt.get("finished_at"),
                "database_sha256": attempt.get("database_sha256"),
                "commit": attempt.get("commit"),
            }
        elif attempt.get("outcome") == "no_change" and accepted is None:
            accepted = {
                "run_id": attempt.get("run_id"),
                "accepted_at": attempt.get("finished_at"),
                "database_sha256": attempt.get("database_sha256"),
                "commit": attempt.get("commit"),
            }
        document: dict[str, object] = {
            "schema": 2,
            "last_attempt": dict(attempt),
        }
        if accepted:
            document["last_accepted"] = accepted
            document["last_accepted_run_id"] = accepted.get("run_id")
            document["last_accepted_at"] = accepted.get("accepted_at")
        # Preserve legacy top-level attempt fields for installed readers during
        # the schema transition.
        document.update(attempt)
        atomic_json(self.config.promotion_state, document)
        return document

    def auto_enabled(self) -> bool:
        if not self.config.enable_marker.exists():
            return False
        require_regular(self.config.enable_marker, uid=0, mode=0o644)
        return True

    def refuse_rejected_replay(self, candidate: Candidate) -> None:
        document = self.state_document()
        if not document:
            return
        previous = document.get("last_attempt")
        if not isinstance(previous, dict):
            previous = document
        if previous.get("database_sha256") != candidate.sha256:
            return
        if previous.get("outcome") in {"rejected", "rolled_back", "rollback_failed"}:
            raise PromotionSkipped(
                "candidate_previously_rejected",
                "automatic promotion will not retry the same rejected candidate",
            )

    def previous_state(self) -> dict[str, object] | None:
        document = self.state_document()
        if not document:
            return None
        attempt = document.get("last_attempt")
        return attempt if isinstance(attempt, dict) else document

    def delivery_attempt(self) -> dict[str, object]:
        require_regular(self.config.delivery_state, uid=self.uid, mode=0o600)
        delivery = read_json(self.config.delivery_state)
        attempt = delivery.get("last_attempt")
        if not isinstance(attempt, dict):
            attempt = delivery.get("last_shadow_attempt")
        if not isinstance(attempt, dict):
            raise PromotionError(
                "shadow_not_validated", "latest shadow attempt is unavailable")
        return attempt

    def candidate(self, expected_run_id: str | None = None,
                  expected_sha256: str | None = None) -> Candidate:
        root = self.config.candidate_root
        require_directory(root, uid=self.uid, mode=0o700)
        database = root / "timetable.db"
        manifest_path = root / "manifest.json"
        attribution = root / "TIMETABLE_ARTIFACT_ATTRIBUTION.txt"
        for path in (database, manifest_path, attribution):
            require_regular(path, uid=self.uid, mode=0o600)

        delivery = read_json(self.config.delivery_state)
        attempt = delivery.get("last_attempt")
        if not isinstance(attempt, dict):
            attempt = delivery.get("last_shadow_attempt")
        if not isinstance(attempt, dict) or attempt.get("outcome") != "success":
            raise PromotionError("shadow_not_validated", "latest shadow attempt did not succeed")
        run_id = str(attempt.get("run_id", ""))
        commit = str(attempt.get("commit", ""))
        expected_hash = str(attempt.get("database_sha256", ""))
        if not run_id.isdigit() or len(commit) != 40 or len(expected_hash) != 64:
            raise PromotionError("invalid_state", "shadow success identity is incomplete")
        if expected_run_id is not None and run_id != expected_run_id:
            raise PromotionError(
                "attended_identity_mismatch",
                "attended promotion run does not match the reviewed shadow")
        if expected_sha256 is not None and expected_hash != expected_sha256:
            raise PromotionError(
                "attended_identity_mismatch",
                "attended promotion hash does not match the reviewed shadow")
        if str(delivery.get("last_shadow_run_id", "")) != run_id:
            raise PromotionError("invalid_state", "shadow run identity is inconsistent")

        manifest = read_json(manifest_path)
        builder = manifest.get("builder")
        if not isinstance(builder, dict) \
                or str(builder.get("workflow_run_id")) != run_id \
                or builder.get("commit") != commit:
            raise PromotionError("invalid_manifest", "candidate provenance differs from shadow state")
        try:
            validation = verify_manifest(
                database=database,
                manifest_path=manifest_path,
                minimum_service_days=self.config.minimum_service_days,
            )
            comparison = compare_with_current(
                self.config.live_root / "timetable.db",
                database,
                validation,
                start_date=self.now().astimezone(BRISTOL_TZ).date(),
            )
        except Exception as exc:
            code = exc.code if hasattr(exc, "code") else "candidate_validation_failed"
            context = exc.safe_context if hasattr(exc, "safe_context") else {}
            raise PromotionError(
                str(code), "candidate failed root-side validation", context) from exc
        actual_hash = sha256_file(database)
        if actual_hash != expected_hash:
            raise PromotionError("candidate_changed", "candidate hash differs from shadow success")
        recorded_validation = attempt.get("validation")
        if not isinstance(recorded_validation, dict) or validation != recorded_validation:
            raise PromotionError("candidate_changed", "candidate counts differ from shadow success")
        sources = manifest.get("sources")
        tnds = sources.get("tnds") if isinstance(sources, dict) else None
        tnds_status = tnds.get("status") if isinstance(tnds, dict) else None
        if tnds_status not in {"not_needed", "fallback_used"}:
            raise PromotionError("invalid_manifest", "candidate source decision is missing")
        return Candidate(
            run_id, commit, actual_hash, validation, comparison, tnds_status)

    @staticmethod
    def _safe_remove(path: Path, allowed_uids: set[int]) -> None:
        if not path.exists() and not path.is_symlink():
            return
        details = require_regular(path)
        if details.st_uid not in allowed_uids:
            raise PromotionError("unsafe_owner", f"refusing stale file owner: {path.name}")
        path.unlink()

    def stage(self, candidate: Candidate) -> None:
        live, upload, previous, failed = paths(self.config.live_root)
        require_directory(self.config.live_root, uid=self.uid, mode=0o750)
        require_regular(live, uid=self.uid, mode=0o600)
        self._safe_remove(upload, {0, self.uid})
        self._safe_remove(failed, {0, self.uid})
        if previous.exists() or previous.is_symlink():
            require_regular(previous, uid=self.uid, mode=0o600)

        temporary = self.config.live_root / ".timetable.db.promoting"
        self._safe_remove(temporary, {0})
        source = self.config.candidate_root / "timetable.db"
        source_fd = os.open(source, os.O_RDONLY | NOFOLLOW)
        destination_fd = -1
        digest = hashlib.sha256()
        try:
            destination_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW,
                0o600,
            )
            with os.fdopen(source_fd, "rb", closefd=True) as incoming, \
                    os.fdopen(destination_fd, "wb", closefd=True) as outgoing:
                source_fd = destination_fd = -1
                for block in iter(lambda: incoming.read(COPY_CHUNK), b""):
                    digest.update(block)
                    outgoing.write(block)
                outgoing.flush()
                os.fsync(outgoing.fileno())
                os.fchmod(outgoing.fileno(), 0o600)
                if hasattr(os, "fchown"):
                    os.fchown(outgoing.fileno(), self.uid, self.gid)
            if digest.hexdigest() != candidate.sha256:
                raise PromotionError("candidate_changed", "candidate changed while staging")
            os.replace(temporary, upload)
            staged = validate(
                upload, minimum_service_days=self.config.minimum_service_days,
                require_stop_routes=True)
            if staged != candidate.validation or sha256_file(upload) != candidate.sha256:
                raise PromotionError("staging_changed", "root staging validation differs")
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            if destination_fd >= 0:
                os.close(destination_fd)
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()

    def _restart_and_check(self) -> None:
        for component in ("collector", "site", "bot"):
            self.services.restart(component)
            self.fault(f"restart:{component}")
            if not self.services.wait_component(component):
                raise PromotionError(
                    "consumer_unhealthy", f"{component} rejected the promoted timetable")
            self.fault(f"health:{component}")
        if not self.services.wait_public():
            raise PromotionError("public_unhealthy", "public health rejected the timetable")
        self.fault("public_health")

    def _rollback_and_raise(self, state: dict[str, object],
                            failure: PromotionError,
                            previous_hash: str) -> None:
        live = self.config.live_root / "timetable.db"
        try:
            rollback_result = rollback(self.config.live_root)
            recovered = bool(rollback_result) and sha256_file(live) == previous_hash
            for component in ("collector", "site", "bot"):
                self.services.restart(component)
                recovered = self.services.wait_component(component) and recovered
            recovered = self.services.wait_public() and recovered
            state.update({
                "outcome": "rolled_back" if recovered else "rollback_failed",
                "finished_at": self.now().isoformat(),
                "failure_code": failure.code,
                "error": str(failure),
                "context": failure.safe_context,
                "recovery_healthy": bool(recovered),
            })
            self.write_state(state)
            if not recovered:
                raise PromotionError(
                    "rollback_failed", "previous timetable returned but health did not recover")
        except PromotionError:
            raise
        except Exception as rollback_exc:
            state.update({
                "outcome": "rollback_failed",
                "finished_at": self.now().isoformat(),
                "failure_code": failure.code,
                "error": "automatic timetable rollback failed",
                "context": failure.safe_context,
                "recovery_healthy": False,
            })
            self.write_state(state)
            raise PromotionError("rollback_failed", "automatic timetable rollback failed") \
                from rollback_exc
        raise PromotionError(
            "rolled_back", "candidate was rejected and the previous timetable restored") \
            from failure

    def _resume_interrupted(self, candidate: Candidate,
                            state: dict[str, object]) -> dict[str, object]:
        previous_hash = str(state.get("previous_sha256", ""))
        previous = self.config.live_root / "timetable.db.previous"
        if len(previous_hash) != 64:
            raise PromotionError(
                "interrupted_state_invalid", "interrupted transaction has no previous hash")
        require_regular(previous, uid=self.uid, mode=0o600)
        if sha256_file(previous) != previous_hash:
            raise PromotionError(
                "interrupted_state_invalid", "interrupted rollback copy has changed")
        state["resumed_at"] = self.now().isoformat()
        self.write_state(state)
        try:
            self._restart_and_check()
        except Exception as exc:
            failure = exc if isinstance(exc, PromotionError) else PromotionError(
                "promotion_failed", "promotion health transaction failed")
            self._rollback_and_raise(state, failure, previous_hash)
        finished = self.now()
        state.update({
            "outcome": "accepted",
            "finished_at": finished.isoformat(),
            "recovered_interrupted_transaction": True,
        })
        self.write_state(state)
        return state

    def run(self, request: PromotionRequest | str) -> dict[str, object]:
        if isinstance(request, str):
            request = promotion_request(request)
        mode = request.mode
        if mode == "auto" and not self.auto_enabled():
            raise PromotionSkipped("promotion_disabled", "automatic promotion is disabled")

        attempt_id = uuid.uuid4().hex
        if mode == "auto":
            delivery_attempt = self.delivery_attempt()
            if delivery_attempt.get("outcome") != "success":
                raise PromotionSkipped(
                    "shadow_not_new", "no new successful shadow requires promotion")
            if delivery_attempt.get("mode") != "auto":
                raise PromotionSkipped(
                    "shadow_not_automatic",
                    "automatic promotion cannot consume an attended shadow")
            run_id = str(delivery_attempt.get("run_id") or "")
            sha256 = str(delivery_attempt.get("database_sha256") or "")
            if not run_id.isdigit() or len(sha256) != 64:
                raise PromotionError(
                    "invalid_state", "successful shadow identity is incomplete")
            document = self.state_document()
            previous = document.get("last_attempt")
            if not isinstance(previous, dict):
                previous = document
            same_attempt = (
                str(previous.get("run_id") or "") == run_id
                and str(previous.get("database_sha256") or "") == sha256
            )
            if same_attempt and previous.get("outcome") in {
                    "rejected", "rolled_back", "rollback_failed"}:
                raise PromotionSkipped(
                    "candidate_previously_rejected",
                    "automatic promotion will not retry the same rejected candidate")
            if same_attempt and previous.get("outcome") in {"accepted", "no_change"}:
                raise PromotionSkipped(
                    "candidate_already_handled",
                    "latest successful shadow was already handled")
            accepted = self._last_accepted(document)
            if accepted and accepted.get("database_sha256") == sha256:
                payload = {
                    "attempt_id": attempt_id,
                    "outcome": "no_change",
                    "mode": mode,
                    "finished_at": self.now().isoformat(),
                    "run_id": run_id,
                    "commit": delivery_attempt.get("commit"),
                    "database_sha256": sha256,
                    "validation": delivery_attempt.get("validation"),
                    "comparison": delivery_attempt.get("comparison"),
                    "tnds_status": delivery_attempt.get("tnds_status"),
                }
                self.write_state(payload)
                return payload

        try:
            candidate = self.candidate(request.run_id, request.sha256)
            if mode == "auto":
                self.refuse_rejected_replay(candidate)
        except PromotionSkipped:
            raise
        except PromotionError as exc:
            self.write_state({
                "attempt_id": attempt_id,
                "outcome": "rejected",
                "finished_at": self.now().isoformat(),
                "run_id": request.run_id,
                "database_sha256": request.sha256,
                "failure_code": exc.code,
                "error": str(exc),
                "context": exc.safe_context,
            })
            raise
        live = self.config.live_root / "timetable.db"
        require_regular(live, uid=self.uid, mode=0o600)
        before_hash = sha256_file(live)
        if before_hash == candidate.sha256:
            previous_state = self.previous_state()
            if previous_state and previous_state.get("outcome") == "running":
                if previous_state.get("database_sha256") != candidate.sha256 \
                        or str(previous_state.get("run_id")) != candidate.run_id:
                    raise PromotionError(
                        "interrupted_state_invalid",
                        "running transaction does not identify the live candidate",
                    )
                return self._resume_interrupted(candidate, previous_state)
            payload = {
                "attempt_id": attempt_id,
                "outcome": "no_change",
                "mode": mode,
                "finished_at": self.now().isoformat(),
                "run_id": candidate.run_id,
                "commit": candidate.commit,
                "database_sha256": candidate.sha256,
                "validation": candidate.validation,
                "comparison": candidate.comparison,
                "tnds_status": candidate.tnds_status,
            }
            self.write_state(payload)
            return payload

        started = self.now()
        state: dict[str, object] = {
            "attempt_id": attempt_id,
            "outcome": "running",
            "mode": mode,
            "started_at": started.isoformat(),
            "run_id": candidate.run_id,
            "commit": candidate.commit,
            "database_sha256": candidate.sha256,
            "previous_sha256": before_hash,
            "validation": candidate.validation,
            "comparison": candidate.comparison,
            "tnds_status": candidate.tnds_status,
        }
        self.write_state(state)
        promoted = False
        try:
            self.stage(candidate)
            self.fault("before_replace")
            promoted_result = promote(self.config.live_root)
            promoted = True
            self.fault("after_replace")
            if promoted_result != candidate.validation \
                    or sha256_file(live) != candidate.sha256:
                raise PromotionError("live_changed", "promoted timetable differs from candidate")
            self._restart_and_check()
        except Exception as exc:
            failure = exc if isinstance(exc, PromotionError) else PromotionError(
                "promotion_failed", "promotion transaction failed")
            if not promoted:
                _, upload, _, _ = paths(self.config.live_root)
                self._safe_remove(upload, {0, self.uid})
                state.update({
                    "outcome": "rejected",
                    "finished_at": self.now().isoformat(),
                    "failure_code": failure.code,
                    "error": str(failure),
                    "context": failure.safe_context,
                })
                self.write_state(state)
                raise failure
            self._rollback_and_raise(state, failure, before_hash)

        finished = self.now()
        state.update({
            "outcome": "accepted",
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
        })
        self.write_state(state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=promotion_request, required=True,
        help="use 'auto' or the exact reviewed RUN_ID-SHA256")
    args = parser.parse_args()
    try:
        result = TimetablePromoter(PromotionConfig(), SystemServices()).run(
            args.candidate)
    except PromotionSkipped as exc:
        print(json.dumps({"status": "skipped", "reason": exc.code}))
        return 75
    except PromotionError as exc:
        print(json.dumps({"status": "failed", "reason": exc.code}))
        return 1
    print(json.dumps({
        "status": result["outcome"],
        "run_id": result["run_id"],
        "database_sha256": result["database_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
