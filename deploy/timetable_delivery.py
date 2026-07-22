#!/usr/bin/env python3
"""Trigger, fetch, and validate a GitHub timetable parcel without promoting it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from timetable_control import VALIDATOR_ID, validate
from timetable_manifest import (
    GTFS_OPTIONAL,
    GTFS_REQUIRED,
    MANIFEST_VERSION,
    verify_manifest,
)


REPOSITORY = "bristol-bus-bot/bristolbusbot"
WORKFLOW_FILE = "timetable-build.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
DEFAULT_BRANCH = "main"
ARTIFACT_NAME = "bristolbusbot-timetable"
ALLOWED_EVENTS = {"workflow_dispatch"}
ALLOWED_FILES = {
    "timetable.db",
    "manifest.json",
    "TIMETABLE_ARTIFACT_ATTRIBUTION.txt",
}
SHADOW_ROOT = Path("/var/lib/bristolbusbot/timetable-shadow")
LIVE_DATABASE = Path("/var/lib/bristolbusbot/pipeline/timetable.db")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = MAX_DATABASE_BYTES + 2 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MINIMUM_SERVICE_DAYS = 14
COVERAGE_WARNING_DAYS = 28
MAX_ARTIFACT_AGE = timedelta(days=7)
MINIMUM_REFRESH_INTERVAL = timedelta(days=6)
SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
BRISTOL_TZ = ZoneInfo("Europe/London")
COUNT_RATIOS = {
    "routes": 0.80,
    "trips": 0.75,
    "stops": 0.80,
    "stop_times": 0.75,
    "route_shapes": 0.70,
    "first_routes": 0.80,
    "stop_routes": 0.70,
}


class DeliveryError(RuntimeError):
    """A named safety gate refused a timetable delivery."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DeliverySkipped(DeliveryError):
    """A healthy idempotent/no-refresh outcome, reported with exit code 75."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DeliveryError("malformed_metadata", f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryError("malformed_metadata", f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DeliveryError("malformed_metadata", f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = path.with_name(f".{path.name}.new-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError("invalid_local_state", f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeliveryError("invalid_local_state", f"{path.name} is not a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow only HTTPS artifact redirects and never forward the API token."""

    ALLOWED_SUFFIXES = (
        ".githubusercontent.com",
        ".github.com",
        ".blob.core.windows.net",
    )

    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(
            request, fp, code, message, headers, new_url)
        if redirected is None:
            return None
        parts = urllib.parse.urlsplit(new_url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(
                host == suffix[1:] or host.endswith(suffix)
                for suffix in self.ALLOWED_SUFFIXES):
            raise DeliveryError("unsafe_redirect", "GitHub returned an untrusted artifact redirect")
        if host != "api.github.com":
            for collection in (redirected.headers, redirected.unredirected_hdrs):
                collection.pop("Authorization", None)
                collection.pop("authorization", None)
        return redirected


class GitHubClient:
    def __init__(self, token: str | None, *,
                 opener=None, sleeper: Callable[[float], None] = time.sleep):
        self.token = token or ""
        self.opener = opener or urllib.request.build_opener(SafeRedirectHandler())
        self.sleeper = sleeper
        self.api = f"https://api.github.com/repos/{REPOSITORY}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "bristolbusbot-timetable-delivery/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def json(self, method: str, path: str, payload: dict | None = None):
        body = (json.dumps(payload).encode("utf-8") if payload is not None else None)
        attempts = 1 if method == "POST" else 3
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                self.api + path, data=body, method=method,
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            try:
                with self.opener.open(request, timeout=30) as response:
                    raw = response.read(MAX_JSON_BYTES + 1)
                    if len(raw) > MAX_JSON_BYTES:
                        raise DeliveryError("malformed_api", "GitHub API response is too large")
                    if not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                retryable = exc.code in {429, 500, 502, 503, 504}
                if retryable and attempt < attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise DeliveryError(
                    "github_api_error", f"GitHub API returned HTTP {exc.code} for {path.split('?')[0]}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise DeliveryError("github_api_error", "GitHub API could not be reached") from exc
            except json.JSONDecodeError as exc:
                raise DeliveryError("malformed_api", "GitHub API returned invalid JSON") from exc
        raise AssertionError("unreachable")

    def workflow(self) -> dict:
        value = self.json("GET", f"/actions/workflows/{WORKFLOW_FILE}")
        if not isinstance(value, dict):
            raise DeliveryError("malformed_api", "workflow response is not an object")
        return value

    def enable_workflow(self) -> None:
        self.json("PUT", f"/actions/workflows/{WORKFLOW_FILE}/enable")

    def runs(self) -> list[dict]:
        value = self.json(
            "GET", f"/actions/workflows/{WORKFLOW_FILE}/runs?branch={DEFAULT_BRANCH}&per_page=20")
        runs = value.get("workflow_runs") if isinstance(value, dict) else None
        if not isinstance(runs, list) or not all(isinstance(item, dict) for item in runs):
            raise DeliveryError("malformed_api", "workflow run list is invalid")
        return runs

    def run(self, run_id: int) -> dict:
        value = self.json("GET", f"/actions/runs/{run_id}")
        if not isinstance(value, dict):
            raise DeliveryError("malformed_api", "workflow run response is invalid")
        return value

    def dispatch(self) -> None:
        self.json(
            "POST", f"/actions/workflows/{WORKFLOW_FILE}/dispatches",
            {"ref": DEFAULT_BRANCH})

    def artifacts(self, run_id: int) -> list[dict]:
        value = self.json("GET", f"/actions/runs/{run_id}/artifacts?per_page=10")
        artifacts = value.get("artifacts") if isinstance(value, dict) else None
        if not isinstance(artifacts, list) or not all(
                isinstance(item, dict) for item in artifacts):
            raise DeliveryError("malformed_api", "artifact list is invalid")
        return artifacts

    def download(self, artifact_id: int, destination: Path,
                 expected_digest: str) -> int:
        if not self.token:
            raise DeliveryError("missing_token", "GitHub delivery token is not configured")
        expected = expected_digest.removeprefix("sha256:")
        if not SHA256_RE.fullmatch(expected):
            raise DeliveryError("malformed_artifact", "artifact has no valid SHA-256 digest")
        url = f"{self.api}/actions/artifacts/{artifact_id}/zip"
        for attempt in range(1, 4):
            destination.unlink(missing_ok=True)
            request = urllib.request.Request(url, headers=self._headers())
            started = time.monotonic()
            try:
                with self.opener.open(request, timeout=60) as response:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > MAX_ARCHIVE_BYTES:
                        raise DeliveryError("artifact_too_large", "artifact archive exceeds the byte limit")
                    digest = hashlib.sha256()
                    total = 0
                    with destination.open("xb") as output:
                        while block := response.read(1024 * 1024):
                            total += len(block)
                            if total > MAX_ARCHIVE_BYTES:
                                raise DeliveryError(
                                    "artifact_too_large", "artifact archive exceeds the byte limit")
                            if time.monotonic() - started > 15 * 60:
                                raise DeliveryError("download_timeout", "artifact download exceeded 15 minutes")
                            digest.update(block)
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                if total == 0:
                    raise DeliveryError("empty_artifact", "artifact download was empty")
                if digest.hexdigest() != expected:
                    raise DeliveryError("archive_hash_mismatch", "downloaded artifact digest does not match GitHub")
                os.chmod(destination, 0o600)
                return total
            except DeliveryError:
                destination.unlink(missing_ok=True)
                raise
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                destination.unlink(missing_ok=True)
                if attempt < 3:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise DeliveryError("download_failed", "artifact download failed after three attempts") from exc
        raise AssertionError("unreachable")


def read_github_token() -> str | None:
    credentials = os.getenv("CREDENTIALS_DIRECTORY")
    if credentials:
        path = Path(credentials) / "github-token"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 513:
                raise DeliveryError("missing_token", "systemd GitHub credential is unsafe")
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DeliveryError("missing_token", "systemd GitHub credential is unreadable") from exc
        if len(token) < 20 or any(character.isspace() for character in token):
            raise DeliveryError("missing_token", "systemd GitHub credential is invalid")
        return token
    return os.getenv("BBB_GITHUB_TOKEN")


@dataclass(frozen=True)
class DeliveryConfig:
    shadow_root: Path = SHADOW_ROOT
    live_database: Path = LIVE_DATABASE
    coverage_warning_days: int = COVERAGE_WARNING_DAYS
    minimum_service_days: int = MINIMUM_SERVICE_DAYS
    max_artifact_age: timedelta = MAX_ARTIFACT_AGE
    minimum_refresh_interval: timedelta = MINIMUM_REFRESH_INTERVAL
    poll_timeout_seconds: int = 45 * 60
    discovery_timeout_seconds: int = 2 * 60

    @property
    def state_path(self) -> Path:
        return self.shadow_root / "state.json"

    @property
    def candidate_path(self) -> Path:
        return self.shadow_root / "candidate"


def ensure_shadow_boundary(config: DeliveryConfig) -> None:
    shadow = config.shadow_root.absolute()
    live = config.live_database.absolute()
    if not shadow.is_absolute() or shadow == Path(shadow.anchor):
        raise DeliveryError("unsafe_shadow_path", "shadow root is not a safe absolute path")
    if live == shadow or live.is_relative_to(shadow):
        raise DeliveryError("unsafe_shadow_path", "shadow root could contain the live timetable")
    if shadow.is_relative_to(live.parent):
        raise DeliveryError("unsafe_shadow_path", "shadow root cannot be inside the live timetable directory")


def validate_workflow(value: dict) -> int:
    workflow_id = value.get("id")
    if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
        raise DeliveryError("wrong_workflow", "workflow has no numeric ID")
    if value.get("path") != WORKFLOW_PATH:
        raise DeliveryError("wrong_workflow", "GitHub returned an unexpected workflow path")
    state = value.get("state")
    if state not in {"active", "disabled_inactivity"}:
        raise DeliveryError("workflow_unavailable", f"workflow state is {state!r}")
    return workflow_id


def validate_run_identity(run: dict, workflow_id: int, *,
                          require_success: bool = False) -> None:
    if run.get("workflow_id") != workflow_id:
        raise DeliveryError("wrong_run", "run belongs to a different workflow")
    if run.get("head_branch") != DEFAULT_BRANCH:
        raise DeliveryError("wrong_run", "run is not from the default branch")
    if run.get("event") not in ALLOWED_EVENTS:
        raise DeliveryError("wrong_run", "run event is not allowed")
    if str(run.get("path", "")).split("@", 1)[0] != WORKFLOW_PATH:
        raise DeliveryError("wrong_run", "run names an unexpected workflow path")
    repository = run.get("head_repository")
    if not isinstance(repository, dict) or repository.get("full_name") != REPOSITORY:
        raise DeliveryError("wrong_run", "run came from an unexpected repository")
    if not SHA_RE.fullmatch(str(run.get("head_sha", ""))):
        raise DeliveryError("wrong_run", "run has an invalid commit SHA")
    if require_success and (
            run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise DeliveryError("build_not_successful", "selected workflow run did not succeed")


def extract_safely(archive: Path, destination: Path) -> None:
    limits = {
        "timetable.db": MAX_DATABASE_BYTES,
        "manifest.json": 1024 * 1024,
        "TIMETABLE_ARTIFACT_ATTRIBUTION.txt": 64 * 1024,
    }
    try:
        with zipfile.ZipFile(archive) as package:
            infos = package.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or set(names) != ALLOWED_FILES:
                raise DeliveryError("unsafe_archive", "artifact does not contain exactly the allowed files")
            total = 0
            for info in infos:
                path = PurePosixPath(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if (info.is_dir() or path.is_absolute() or len(path.parts) != 1
                        or ".." in path.parts or "\\" in info.filename
                        or info.flag_bits & 0x1
                        or file_type not in {0, stat.S_IFREG}):
                    raise DeliveryError("unsafe_archive", "artifact contains an unsafe ZIP entry")
                if info.file_size <= 0 or info.file_size > limits[info.filename]:
                    raise DeliveryError("unsafe_archive", f"{info.filename} has an unsafe size")
                total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise DeliveryError("unsafe_archive", "artifact expands beyond the byte limit")
            destination.mkdir(mode=0o700)
            for info in infos:
                target = destination / info.filename
                copied = 0
                with package.open(info) as source, target.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        copied += len(block)
                        if copied > info.file_size:
                            raise DeliveryError("unsafe_archive", "ZIP entry exceeded its declared size")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if copied != info.file_size:
                    raise DeliveryError("unsafe_archive", "ZIP entry was truncated")
                os.chmod(target, 0o600)
            bad = package.testzip()
            if bad is not None:
                raise DeliveryError("unsafe_archive", f"artifact CRC failed for {bad}")
    except DeliveryError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise DeliveryError("unsafe_archive", "artifact is not a safe readable ZIP") from exc


def validate_file_record(record: object, source: str) -> None:
    if not isinstance(record, dict):
        raise DeliveryError("invalid_manifest", f"{source} source record is invalid")
    name = record.get("name")
    path = PurePosixPath(name) if isinstance(name, str) else None
    if (not isinstance(name, str) or not name
            or path is None or path.is_absolute() or len(path.parts) != 1
            or ".." in path.parts or "\\" in name
            or isinstance(record.get("bytes"), bool)
            or not isinstance(record.get("bytes"), int)
            or record["bytes"] <= 0
            or not SHA256_RE.fullmatch(str(record.get("sha256", "")))):
        raise DeliveryError("invalid_manifest", f"{source} source record is incomplete")
    parse_utc(record.get("modified_utc"), f"{source}.modified_utc")


def validate_manifest_identity(manifest: dict, run: dict, artifact: dict,
                               now: datetime) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise DeliveryError("unsupported_manifest", "parcel manifest version is unsupported")
    builder = manifest.get("builder")
    if not isinstance(builder, dict):
        raise DeliveryError("invalid_manifest", "manifest has no builder record")
    if (builder.get("repository") != REPOSITORY
            or builder.get("ref") != f"refs/heads/{DEFAULT_BRANCH}"
            or builder.get("commit") != run.get("head_sha")
            or str(builder.get("workflow_run_id")) != str(run.get("id"))):
        raise DeliveryError("provenance_mismatch", "manifest builder does not match the selected run")

    build = manifest.get("build")
    if not isinstance(build, dict):
        raise DeliveryError("invalid_manifest", "manifest has no build timing record")
    started = parse_utc(build.get("started_utc"), "build.started_utc")
    finished = parse_utc(build.get("finished_utc"), "build.finished_utc")
    created = parse_utc(manifest.get("created_utc"), "created_utc")
    run_created = parse_utc(run.get("created_at"), "run.created_at")
    artifact_created = parse_utc(artifact.get("created_at"), "artifact.created_at")
    if (started > finished or abs((created - finished).total_seconds()) > 5
            or started < run_created - timedelta(minutes=5)
            or finished > artifact_created + timedelta(minutes=5)
            or finished > now + timedelta(minutes=5)
            or now - finished > MAX_ARTIFACT_AGE):
        raise DeliveryError("provenance_mismatch", "manifest build timestamps are inconsistent")

    validation_record = manifest.get("validation")
    if (not isinstance(validation_record, dict)
            or validation_record.get("validator") != VALIDATOR_ID
            or validation_record.get("minimum_service_days") != MINIMUM_SERVICE_DAYS):
        raise DeliveryError("invalid_manifest", "manifest validation contract is unsupported")
    licence = manifest.get("licence")
    if licence != {
        "identifier": "OGL-3.0",
        "attribution_file": "TIMETABLE_ARTIFACT_ATTRIBUTION.txt",
    }:
        raise DeliveryError("invalid_manifest", "manifest licence record is unsupported")

    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        raise DeliveryError("invalid_manifest", "manifest has no source records")
    bods = sources.get("bods_gtfs")
    first = sources.get("first_txc")
    tnds = sources.get("tnds")
    if not isinstance(bods, dict) or not isinstance(bods.get("files"), list) \
            or len(bods["files"]) < 7:
        raise DeliveryError("invalid_manifest", "manifest has incomplete BODS provenance")
    if (not isinstance(first, dict) or first.get("status") != "used"
            or not isinstance(first.get("files"), list) or not first["files"]):
        raise DeliveryError("invalid_manifest", "manifest has incomplete First TXC provenance")
    if not isinstance(tnds, dict) or tnds.get("status") not in {
            "not_needed", "fallback_used"}:
        raise DeliveryError("invalid_manifest", "manifest has an invalid TNDS decision")
    missing = tnds.get("missing_before_fallback")
    tnds_files = tnds.get("files")
    if not isinstance(missing, list) or not isinstance(tnds_files, list):
        raise DeliveryError("invalid_manifest", "manifest has invalid TNDS provenance")
    if ((tnds["status"] == "not_needed" and (missing or tnds_files))
            or (tnds["status"] == "fallback_used" and (not missing or not tnds_files))):
        raise DeliveryError("invalid_manifest", "manifest TNDS decision is inconsistent")
    if not all(isinstance(route, str) and route for route in missing):
        raise DeliveryError("invalid_manifest", "manifest TNDS missing-route list is invalid")
    for name, records in (
            ("bods_gtfs", bods["files"]),
            ("first_txc", first["files"]),
            ("tnds", tnds_files)):
        for record in records:
            validate_file_record(record, name)
    bods_names = [record["name"] for record in bods["files"]]
    if (len(bods_names) != len(set(bods_names))
            or not set(GTFS_REQUIRED).issubset(bods_names)
            or not set(bods_names).issubset(set(GTFS_REQUIRED) | set(GTFS_OPTIONAL))):
        raise DeliveryError("invalid_manifest", "manifest BODS file set is invalid")
    for source, records in (("first_txc", first["files"]), ("tnds", tnds_files)):
        names = [record["name"] for record in records]
        if len(names) != len(set(names)) or any(
                not value.lower().endswith(".zip") for value in names):
            raise DeliveryError("invalid_manifest", f"manifest {source} archive set is invalid")


def compare_with_current(current: Path, candidate_result: dict[str, object]) -> dict:
    try:
        current_result = validate(current, today=date(1970, 1, 1))
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise DeliveryError("current_timetable_invalid", "current timetable cannot be compared safely") from exc
    comparisons: dict[str, dict[str, object]] = {}
    for name, ratio in COUNT_RATIOS.items():
        new = int(candidate_result[name])
        if name not in current_result:
            comparisons[name] = {
                "current": None,
                "candidate": new,
                "minimum": 1,
                "schema_migration": True,
            }
            continue
        old = int(current_result[name])
        minimum = int(old * ratio)
        comparisons[name] = {"current": old, "candidate": new, "minimum": minimum}
        if old > 0 and new < minimum:
            raise DeliveryError(
                "candidate_count_collapse",
                f"candidate {name} count {new} is below safe minimum {minimum}")
    return comparisons


def remove_shadow_tree(path: Path, root: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    absolute = path.absolute()
    if absolute.parent != root.absolute() or path.is_symlink():
        raise DeliveryError("unsafe_shadow_path", "refusing unsafe shadow cleanup")
    shutil.rmtree(path)


class TimetableDelivery:
    def __init__(self, config: DeliveryConfig, client: GitHubClient, *,
                 now: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 token_expires_utc: str | None = None):
        self.config = config
        self.client = client
        self.now = now
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.token_expires_utc = token_expires_utc
        ensure_shadow_boundary(config)

    def state(self) -> dict[str, object]:
        return load_json(self.config.state_path)

    def write_state(self, payload: dict[str, object]) -> None:
        payload["schema"] = 1
        if self.token_expires_utc:
            payload["token_expires_utc"] = parse_utc(
                self.token_expires_utc, "BBB_GITHUB_TOKEN_EXPIRES_UTC").isoformat()
        atomic_json(self.config.state_path, payload)

    def coverage_urgent(self) -> tuple[bool, str]:
        try:
            result = validate(self.config.live_database, today=date(1970, 1, 1))
            latest = datetime.strptime(str(result["latest_service"]), "%Y%m%d").date()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            raise DeliveryError("current_timetable_invalid", "current timetable health cannot be read") from exc
        threshold = self.now().astimezone(BRISTOL_TZ).date() + timedelta(
            days=self.config.coverage_warning_days)
        return latest <= threshold, latest.isoformat()

    def _workflow(self) -> tuple[dict, int]:
        workflow = self.client.workflow()
        workflow_id = validate_workflow(workflow)
        if workflow.get("state") == "disabled_inactivity":
            self.client.enable_workflow()
            workflow = self.client.workflow()
            workflow_id = validate_workflow(workflow)
            if workflow.get("state") != "active":
                raise DeliveryError("workflow_unavailable", "workflow did not become active")
        return workflow, workflow_id

    def _wait_for_run(self, run_id: int, workflow_id: int) -> dict:
        deadline = self.monotonic() + self.config.poll_timeout_seconds
        delay = 10.0
        while self.monotonic() < deadline:
            run = self.client.run(run_id)
            validate_run_identity(run, workflow_id)
            if run.get("status") == "completed":
                validate_run_identity(run, workflow_id, require_success=True)
                return run
            self.sleeper(delay)
            delay = min(delay * 1.5, 60.0)
        raise DeliveryError("run_timeout", "workflow run did not complete before the polling deadline")

    def _discover_dispatched_run(self, workflow_id: int,
                                 dispatched_at: datetime) -> dict:
        deadline = self.monotonic() + self.config.discovery_timeout_seconds
        while self.monotonic() < deadline:
            for run in self.client.runs():
                try:
                    validate_run_identity(run, workflow_id)
                    created = parse_utc(run.get("created_at"), "run.created_at")
                except DeliveryError:
                    continue
                if created >= dispatched_at - timedelta(minutes=1):
                    return run
            self.sleeper(5)
        raise DeliveryError("dispatch_not_found", "dispatched workflow run could not be identified")

    def select_run(self, workflow_id: int, state: dict[str, object]) -> dict:
        runs = self.client.runs()
        now = self.now()
        last_run = str(state.get("last_shadow_run_id", ""))
        for run in runs:
            try:
                validate_run_identity(run, workflow_id)
            except DeliveryError:
                continue
            if run.get("status") != "completed":
                return run
        for run in runs:
            try:
                validate_run_identity(run, workflow_id, require_success=True)
                created = parse_utc(run.get("created_at"), "run.created_at")
            except DeliveryError:
                continue
            if str(run.get("id")) != last_run and now - created <= timedelta(days=2):
                return run
        dispatched_at = self.now()
        self.client.dispatch()
        return self._discover_dispatched_run(workflow_id, dispatched_at)

    def artifact(self, run: dict) -> dict:
        artifacts = self.client.artifacts(int(run["id"]))
        matches = [item for item in artifacts if item.get("name") == ARTIFACT_NAME]
        if len(artifacts) != 1 or len(matches) != 1:
            raise DeliveryError("wrong_artifact", "run does not contain exactly one approved artifact")
        artifact = matches[0]
        if artifact.get("expired") is not False:
            raise DeliveryError("artifact_expired", "timetable artifact is expired")
        artifact_id = artifact.get("id")
        size = artifact.get("size_in_bytes")
        if (isinstance(artifact_id, bool) or not isinstance(artifact_id, int)
                or isinstance(size, bool) or not isinstance(size, int)
                or size <= 0 or size > MAX_ARCHIVE_BYTES):
            raise DeliveryError("malformed_artifact", "artifact metadata has an unsafe ID or size")
        created = parse_utc(artifact.get("created_at"), "artifact.created_at")
        if created > self.now() + timedelta(minutes=5) \
                or self.now() - created > self.config.max_artifact_age:
            raise DeliveryError("artifact_expired", "timetable artifact is outside the freshness window")
        return artifact

    def deliver(self, run: dict) -> dict[str, object]:
        run_id = int(run["id"])
        artifact = self.artifact(run)
        root = self.config.shadow_root
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        incoming = root / ".incoming"
        remove_shadow_tree(incoming, root)
        incoming.mkdir(mode=0o700)
        archive = incoming / "artifact.zip"
        payload = incoming / "payload"
        try:
            archive_bytes = self.client.download(
                int(artifact["id"]), archive, str(artifact.get("digest", "")))
            extract_safely(archive, payload)
            manifest_path = payload / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DeliveryError("invalid_manifest", "parcel manifest is unreadable") from exc
            if not isinstance(manifest, dict):
                raise DeliveryError("invalid_manifest", "parcel manifest is not an object")
            validate_manifest_identity(manifest, run, artifact, self.now())
            try:
                validation = verify_manifest(
                    database=payload / "timetable.db",
                    manifest_path=manifest_path,
                    minimum_service_days=self.config.minimum_service_days,
                )
            except (OSError, ValueError, sqlite3.Error, RuntimeError,
                    json.JSONDecodeError) as exc:
                raise DeliveryError("candidate_validation_failed", str(exc)) from exc
            attribution = payload / "TIMETABLE_ARTIFACT_ATTRIBUTION.txt"
            text = attribution.read_text(encoding="utf-8")
            if "Open Government Licence" not in text or len(text) > 64 * 1024:
                raise DeliveryError("invalid_attribution", "parcel attribution is missing or invalid")
            comparison = compare_with_current(self.config.live_database, validation)

            archive.unlink()
            old = root / ".candidate-old"
            remove_shadow_tree(old, root)
            if self.config.candidate_path.exists():
                if self.config.candidate_path.is_symlink() \
                        or not self.config.candidate_path.is_dir():
                    raise DeliveryError("unsafe_shadow_path", "existing shadow candidate is unsafe")
                os.replace(self.config.candidate_path, old)
            os.replace(payload, self.config.candidate_path)
            incoming.rmdir()
            remove_shadow_tree(old, root)
            return {
                "run_id": run_id,
                "commit": run["head_sha"],
                "archive_bytes": archive_bytes,
                "database_sha256": sha256_file(
                    self.config.candidate_path / "timetable.db"),
                "validation": validation,
                "comparison": comparison,
                "tnds_status": manifest["sources"]["tnds"]["status"],
            }
        except Exception:
            remove_shadow_tree(incoming, root)
            raise

    def run(self, requested_run_id: int | None = None) -> dict[str, object]:
        started = self.now()
        state = self.state()
        urgent, latest = self.coverage_urgent()
        refresh_due = True
        last_success_value = state.get("last_shadow_success_at")
        if last_success_value:
            last_success = parse_utc(
                last_success_value, "last_shadow_success_at")
            refresh_due = (
                started - last_success >= self.config.minimum_refresh_interval)
        state["last_check"] = {
            "checked_at": started.isoformat(),
            "current_latest_service": latest,
            "coverage_urgent": urgent,
            "coverage_warning_days": self.config.coverage_warning_days,
            "refresh_due": refresh_due,
        }
        if requested_run_id is None and not refresh_due:
            self.write_state(state)
            raise DeliverySkipped("recent_shadow", "a recent shadow delivery already succeeded")
        if not self.client.token:
            raise DeliveryError("missing_token", "GitHub delivery token is not configured")
        if not self.token_expires_utc:
            raise DeliveryError("missing_token_expiry", "GitHub delivery token expiry is not configured")
        token_expiry = parse_utc(
            self.token_expires_utc, "BBB_GITHUB_TOKEN_EXPIRES_UTC")
        if token_expiry <= started:
            raise DeliveryError("expired_token", "GitHub delivery token has expired")

        run: dict[str, object] | None = None
        try:
            _, workflow_id = self._workflow()
            if requested_run_id is not None:
                run = self.client.run(requested_run_id)
                validate_run_identity(run, workflow_id)
            else:
                run = self.select_run(workflow_id, state)
            if str(run.get("id")) == str(state.get("last_shadow_run_id", "")):
                self.write_state(state)
                raise DeliverySkipped(
                    "already_delivered", "selected run was already shadow-delivered")
            state["last_shadow_attempt"] = {
                "started_at": started.isoformat(),
                "outcome": "running",
                "run_id": run.get("id"),
            }
            self.write_state(state)
            run = self._wait_for_run(int(run["id"]), workflow_id)
            result = self.deliver(run)
        except DeliverySkipped:
            raise
        except DeliveryError as exc:
            state["last_shadow_attempt"] = {
                "finished_at": self.now().isoformat(),
                "outcome": "failure",
                "run_id": run.get("id") if run else None,
                "failure_code": exc.code,
            }
            self.write_state(state)
            raise
        finished = self.now()
        state.update({
            "last_shadow_run_id": str(result["run_id"]),
            "last_shadow_success_at": finished.isoformat(),
            "last_shadow_attempt": {
                "finished_at": finished.isoformat(),
                "outcome": "success",
                "run_id": result["run_id"],
                "duration_seconds": round((finished - started).total_seconds(), 3),
                "commit": result["commit"],
                "database_sha256": result["database_sha256"],
                "validation": result["validation"],
                "comparison": result["comparison"],
                "tnds_status": result["tnds_status"],
            },
        })
        self.write_state(state)
        return result


def requested_run_id(value: str) -> int | None:
    if value == "auto":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("run ID must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("run ID must be a positive integer")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", type=requested_run_id,
        help="use 'auto' or one exact run for an attended shadow test; never promotes")
    args = parser.parse_args()
    try:
        config = DeliveryConfig()
        client = GitHubClient(read_github_token())
        delivery = TimetableDelivery(
            config,
            client,
            token_expires_utc=os.getenv("BBB_GITHUB_TOKEN_EXPIRES_UTC"),
        )
        result = delivery.run(args.run_id)
    except DeliverySkipped as exc:
        print(json.dumps({"status": "skipped", "reason": exc.code}))
        return 75
    except DeliveryError as exc:
        print(json.dumps({"status": "failure", "failure_code": exc.code}), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "shadow_validated",
        "run_id": result["run_id"],
        "commit": result["commit"],
        "database_sha256": result["database_sha256"],
        "validation": result["validation"],
        "tnds_status": result["tnds_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
