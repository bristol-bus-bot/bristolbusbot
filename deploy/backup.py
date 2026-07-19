#!/usr/bin/env python3
"""Create validated local and off-site backups of Bristol Bus Bot state.

The runner deliberately separates capture from storage:

1. live SQLite databases are copied with Python's online backup API;
2. the staged copies are checked and described by a manifest;
3. restic writes an encrypted snapshot to the verified local mount;
4. ``restic copy`` transfers snapshots to the configured R2 repository.

Repository passwords and R2 credentials are supplied through root-readable
files/environment variables. Secret values are never accepted as CLI options
or written to the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import AbstractContextManager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


LOG = logging.getLogger("bbb-backup")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DEFAULT_CONFIG = Path("/etc/bristolbusbot/backup.json")
RETENTION = ("--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6")
MIN_RESTIC_VERSION = (0, 18, 0)


class BackupError(RuntimeError):
    """A backup safety gate or operation failed."""


class AlreadyRunning(BackupError):
    """Another process owns the backup lock."""


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    required: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], category: str) -> "Source":
        if not isinstance(raw, dict):
            raise BackupError(f"{category} source must be a JSON object")
        unknown = set(raw) - {"name", "path", "required"}
        if unknown:
            raise BackupError(
                f"unknown {category} source field(s): {', '.join(sorted(unknown))}")
        try:
            name = str(raw["name"])
            path = Path(str(raw["path"]))
        except KeyError as exc:
            raise BackupError(f"{category} source is missing {exc.args[0]}") from exc
        if not NAME_RE.fullmatch(name):
            raise BackupError(f"unsafe {category} source name: {name!r}")
        if not path.is_absolute():
            raise BackupError(f"{category} source must be absolute: {path}")
        if path == Path(path.anchor):
            raise BackupError(f"{category} source must not be a filesystem root")
        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise BackupError(f"{category} source {name!r} has non-boolean required")
        return cls(name=name, path=path, required=required)


@dataclass(frozen=True)
class Config:
    staging_root: Path
    lock_file: Path
    local_mountpoint: Path
    expected_mount_source: str
    local_repository: str
    r2_repository: str
    password_file: Path
    r2_password_file: Path
    sqlite_databases: tuple[Source, ...]
    paths: tuple[Source, ...]
    git_repositories: tuple[Source, ...]
    require_root: bool = True
    require_mounted_local_repository: bool = True
    restic_binary: str = "restic"
    tag: str = "bbb-backup"
    sqlite_timeout_seconds: int = 1800
    restic_timeout_seconds: int = 10800

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(f"cannot read backup config {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise BackupError("backup config must be a JSON object")
        allowed = {
            "staging_root", "lock_file", "local_mountpoint",
            "expected_mount_source", "local_repository", "r2_repository",
            "password_file", "r2_password_file", "sqlite_databases", "paths",
            "git_repositories", "require_root",
            "require_mounted_local_repository", "restic_binary", "tag",
            "sqlite_timeout_seconds", "restic_timeout_seconds",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise BackupError(f"unknown config field(s): {', '.join(sorted(unknown))}")

        required = {
            "staging_root", "lock_file", "local_mountpoint",
            "expected_mount_source", "local_repository", "r2_repository",
            "password_file", "r2_password_file", "sqlite_databases", "paths",
            "git_repositories",
        }
        missing = required - set(raw)
        if missing:
            raise BackupError(f"missing config field(s): {', '.join(sorted(missing))}")

        def absolute(key: str) -> Path:
            value = Path(str(raw[key]))
            if not value.is_absolute():
                raise BackupError(f"{key} must be an absolute path")
            return value

        def sources(key: str) -> tuple[Source, ...]:
            values = raw[key]
            if not isinstance(values, list):
                raise BackupError(f"{key} must be a list")
            parsed = tuple(Source.from_dict(item, key) for item in values)
            names = [item.name for item in parsed]
            if len(names) != len(set(names)):
                raise BackupError(f"duplicate source name in {key}")
            return parsed

        def positive_int(key: str, default: int) -> int:
            value = raw.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise BackupError(f"{key} must be a positive integer")
            return value

        config = cls(
            staging_root=absolute("staging_root"),
            lock_file=absolute("lock_file"),
            local_mountpoint=absolute("local_mountpoint"),
            expected_mount_source=str(raw["expected_mount_source"]),
            local_repository=str(raw["local_repository"]),
            r2_repository=str(raw["r2_repository"]),
            password_file=absolute("password_file"),
            r2_password_file=absolute("r2_password_file"),
            sqlite_databases=sources("sqlite_databases"),
            paths=sources("paths"),
            git_repositories=sources("git_repositories"),
            require_root=raw.get("require_root", True),
            require_mounted_local_repository=raw.get(
                "require_mounted_local_repository", True),
            restic_binary=str(raw.get("restic_binary", "restic")),
            tag=str(raw.get("tag", "bbb-backup")),
            sqlite_timeout_seconds=positive_int("sqlite_timeout_seconds", 1800),
            restic_timeout_seconds=positive_int("restic_timeout_seconds", 10800),
        )
        config.validate()
        return config

    def validate(self) -> None:
        for key, value in (
            ("require_root", self.require_root),
            ("require_mounted_local_repository", self.require_mounted_local_repository),
        ):
            if not isinstance(value, bool):
                raise BackupError(f"{key} must be boolean")
        if not self.expected_mount_source:
            raise BackupError("expected_mount_source must not be empty")
        if not Path(self.expected_mount_source).is_absolute():
            raise BackupError("expected_mount_source must be an absolute device path")
        if not self.local_repository or not self.r2_repository:
            raise BackupError("both restic repositories must be configured")
        r2 = urllib.parse.urlsplit(
            self.r2_repository.removeprefix("s3:"))
        if (
            not self.r2_repository.startswith("s3:https://")
            or r2.scheme != "https"
            or not r2.hostname
            or not r2.hostname.endswith(".r2.cloudflarestorage.com")
            or r2.username is not None
            or r2.password is not None
            or not r2.path.strip("/")
            or r2.query
            or r2.fragment
        ):
            raise BackupError("r2_repository must be a credential-free Cloudflare R2 S3 URL")
        if not NAME_RE.fullmatch(self.tag):
            raise BackupError(f"unsafe restic tag: {self.tag!r}")
        mount = self.local_mountpoint.resolve(strict=False)
        stage = self.staging_root.resolve(strict=False)
        if mount == Path(mount.anchor) or stage == Path(stage.anchor):
            raise BackupError("staging_root and local_mountpoint must not be filesystem roots")
        local_repo = Path(self.local_repository)
        if not local_repo.is_absolute():
            raise BackupError("local_repository must be an absolute filesystem path")
        try:
            local_repo.resolve(strict=False).relative_to(mount)
        except ValueError as exc:
            raise BackupError("local_repository must be below local_mountpoint") from exc
        for source in (*self.sqlite_databases, *self.paths, *self.git_repositories):
            resolved = source.path.resolve(strict=False)
            if resolved == Path(resolved.anchor):
                raise BackupError(f"source {source.name!r} resolves to a filesystem root")
            if _is_relative_to(resolved, stage) or _is_relative_to(stage, resolved):
                raise BackupError(
                    f"source {source.name!r} overlaps the staging directory")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class ProcessLock(AbstractContextManager["ProcessLock"]):
    """A non-blocking advisory lock held for the lifetime of the file handle."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if self.handle.read(1) == b"":
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise AlreadyRunning(f"backup lock is already held: {self.path}") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _decode_mount_path(value: str) -> str:
    return (value.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def mounted_source(mountpoint: Path) -> str | None:
    """Return the Linux mount source for an exact mountpoint."""
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    wanted = str(mountpoint.resolve(strict=False))
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if _decode_mount_path(fields[4]) == wanted and len(fields) > separator + 2:
            return _decode_mount_path(fields[separator + 2])
    return None


def validate_local_mount(config: Config) -> None:
    if not config.require_mounted_local_repository:
        return
    actual = mounted_source(config.local_mountpoint)
    if actual is None:
        raise BackupError(
            f"local backup mount is absent: {config.local_mountpoint}; refusing to use root disk")
    expected_path = Path(config.expected_mount_source)
    actual_path = Path(actual)
    try:
        expected = expected_path.resolve(strict=True)
        observed = actual_path.resolve(strict=True)
    except OSError as exc:
        raise BackupError(f"cannot resolve backup mount device: {exc}") from exc
    if observed != expected:
        raise BackupError(
            f"wrong backup mount source: expected {expected}, observed {observed}")


def validate_private_file(path: Path, label: str, *, require_root_owner: bool) -> None:
    try:
        details = path.stat()
    except OSError as exc:
        raise BackupError(f"missing {label}: {path}") from exc
    if not stat.S_ISREG(details.st_mode):
        raise BackupError(f"{label} is not a regular file: {path}")
    if os.name != "nt" and require_root_owner and details.st_uid != 0:
        raise BackupError(f"{label} must be owned by root: {path}")
    if os.name != "nt" and details.st_mode & 0o077:
        raise BackupError(f"{label} must not be group/world accessible: {path}")


def validate_restic_version(
    binary: str,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> tuple[int, int, int]:
    try:
        result = runner(
            [binary, "version"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackupError(f"cannot run restic version check: {binary}") from exc
    match = re.search(r"\brestic\s+(\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match:
        raise BackupError("cannot parse restic version output")
    version = tuple(int(item) for item in match.groups())
    if version < MIN_RESTIC_VERSION:
        minimum = ".".join(str(item) for item in MIN_RESTIC_VERSION)
        observed = ".".join(str(item) for item in version)
        raise BackupError(f"restic {minimum}+ is required; found {observed}")
    return version


def validate_runtime(
    config: Config,
    *,
    require_local_mount: bool = True,
    require_local_password: bool = True,
    require_r2_credentials: bool = True,
) -> None:
    if config.require_root and hasattr(os, "geteuid") and os.geteuid() != 0:
        raise BackupError("backup must run as root to preserve ownership and read secrets")
    if require_local_mount:
        validate_local_mount(config)
    if shutil.which(config.restic_binary) is None:
        raise BackupError(f"restic executable not found: {config.restic_binary}")
    validate_restic_version(config.restic_binary)
    if require_local_password:
        validate_private_file(
            config.password_file, "local restic password file",
            require_root_owner=config.require_root)
    if require_r2_credentials:
        validate_private_file(
            config.r2_password_file, "R2 restic password file",
            require_root_owner=config.require_root)
        for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if not os.environ.get(key):
                raise BackupError(f"required R2 environment variable is absent: {key}")


class Healthcheck:
    """Best-effort Healthchecks.io start/success/failure signalling."""

    def __init__(
        self,
        url: str | None,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.url = url.strip() if url else ""
        self.run_id = str(uuid.uuid4())
        self.opener = opener
        self.sleeper = sleeper

    def _url(self, state: str) -> str:
        parts = urllib.parse.urlsplit(self.url)
        suffix = "" if state == "success" else f"/{state}"
        path = parts.path.rstrip("/") + suffix
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query.append(("rid", self.run_id))
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, path, urllib.parse.urlencode(query), ""))

    def ping(self, state: str) -> None:
        if not self.url:
            return
        target = self._url(state)
        for attempt in range(3):
            try:
                request = urllib.request.Request(target, method="GET")
                with self.opener(request, timeout=10) as response:
                    response.read(256)
                return
            except (OSError, urllib.error.URLError) as exc:
                if attempt == 2:
                    LOG.warning("healthcheck %s ping failed: %s", state, exc)
                else:
                    self.sleeper(0.5 * (attempt + 1))


class Restic:
    def __init__(
        self,
        config: Config,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ):
        self.config = config
        self.runner = runner

    def _run(self, arguments: Sequence[str]) -> None:
        command = [self.config.restic_binary, *arguments]
        action = next(
            (item for item in arguments
             if item in {"init", "backup", "copy", "forget", "check", "restore"}),
            "command",
        )
        LOG.info("running restic %s", action)
        try:
            self.runner(
                command, check=True, env=os.environ.copy(),
                timeout=self.config.restic_timeout_seconds)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise BackupError(f"restic command failed: {arguments[0]}") from exc

    def init_local(self) -> None:
        self._run([
            "-r", self.config.local_repository,
            "--password-file", str(self.config.password_file), "init",
        ])

    def init_r2(self) -> None:
        self._run([
            "-r", self.config.r2_repository,
            "--password-file", str(self.config.r2_password_file),
            "init", "--from-repo", self.config.local_repository,
            "--from-password-file", str(self.config.password_file),
            "--copy-chunker-params",
        ])

    def backup(self, payload: Path) -> None:
        self._run([
            "-r", self.config.local_repository,
            "--password-file", str(self.config.password_file),
            "backup", str(payload), "--host", socket.gethostname(),
            "--tag", self.config.tag,
        ])

    def copy_to_r2(self) -> None:
        self._run([
            "-r", self.config.r2_repository,
            "--password-file", str(self.config.r2_password_file),
            "copy", "--from-repo", self.config.local_repository,
            "--from-password-file", str(self.config.password_file),
            "--tag", self.config.tag,
        ])

    def forget_local(self) -> None:
        self._run([
            "-r", self.config.local_repository,
            "--password-file", str(self.config.password_file),
            "forget", "--tag", self.config.tag, *RETENTION, "--prune",
        ])

    def forget_r2(self) -> None:
        self._run([
            "-r", self.config.r2_repository,
            "--password-file", str(self.config.r2_password_file),
            "forget", "--tag", self.config.tag, *RETENTION, "--prune",
        ])

    def check_local(self) -> None:
        self._run([
            "-r", self.config.local_repository,
            "--password-file", str(self.config.password_file),
            "check", "--read-data",
        ])

    def check_r2(self, subset: int | None = None) -> None:
        if subset is None:
            continuous_week = datetime.now(timezone.utc).date().toordinal() // 7
            subset = (continuous_week % 4) + 1
        if subset not in range(1, 5):
            raise BackupError("R2 check subset must be between 1 and 4")
        self._run([
            "-r", self.config.r2_repository,
            "--password-file", str(self.config.r2_password_file),
            "check", f"--read-data-subset={subset}/4",
        ])

    def restore(self, repository: str, target: Path) -> None:
        if repository == "local":
            repo = self.config.local_repository
            password = self.config.password_file
        else:
            repo = self.config.r2_repository
            password = self.config.r2_password_file
        self._run([
            "-r", repo, "--password-file", str(password),
            "restore", "latest", "--tag", self.config.tag,
            "--target", str(target),
        ])


def _chown_like(source: Path, destination: Path) -> None:
    if not hasattr(os, "chown"):
        return
    details = source.lstat()
    try:
        os.chown(
            destination, details.st_uid, details.st_gid,
            follow_symlinks=not source.is_symlink())
    except (PermissionError, NotImplementedError):
        # Non-root test/development runs retain ownership in the manifest.
        pass


def _copy_file(source: str, destination: str) -> str:
    result = shutil.copy2(source, destination, follow_symlinks=False)
    _chown_like(Path(source), Path(destination))
    return result


def copy_source(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
        _chown_like(source, destination)
        return
    if source.is_file():
        _copy_file(str(source), str(destination))
        return
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=_copy_file)
        for source_dir, dirnames, _ in os.walk(source, followlinks=False):
            relative = Path(source_dir).relative_to(source)
            copied_dir = destination / relative
            shutil.copystat(source_dir, copied_dir, follow_symlinks=False)
            _chown_like(Path(source_dir), copied_dir)
            for dirname in dirnames:
                original = Path(source_dir) / dirname
                copied = copied_dir / dirname
                if original.is_symlink():
                    _chown_like(original, copied)
        return
    raise BackupError(f"unsupported source type: {source}")


def backup_sqlite(
    source: Path,
    destination: Path,
    *,
    integrity_check: bool,
    timeout_seconds: int,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = source.resolve(strict=True).as_uri() + "?mode=ro"
    deadline = time.monotonic() + timeout_seconds

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise BackupError(
                f"SQLite snapshot exceeded {timeout_seconds}s: {source}")

    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as live:
            live.execute("PRAGMA query_only = ON")
            with closing(sqlite3.connect(destination)) as staged:
                live.backup(staged, pages=4096, progress=progress, sleep=0.01)
                staged.commit()
        pragma = "integrity_check" if integrity_check else "quick_check"
        with closing(sqlite3.connect(destination)) as staged:
            results = [row[0] for row in staged.execute(f"PRAGMA {pragma}")]
        if results != ["ok"]:
            raise BackupError(
                f"SQLite {pragma} failed for {source}: {'; '.join(results[:5])}")
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"cannot snapshot SQLite database {source}: {exc}") from exc
    shutil.copystat(source, destination)
    _chown_like(source, destination)
    return pragma


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(path: Path, relative: Path) -> dict[str, Any]:
    details = path.lstat()
    kind = "symlink" if path.is_symlink() else "file" if path.is_file() else "directory"
    result: dict[str, Any] = {
        "path": relative.as_posix(),
        "type": kind,
        "mode": f"{stat.S_IMODE(details.st_mode):04o}",
        "uid": getattr(details, "st_uid", None),
        "gid": getattr(details, "st_gid", None),
    }
    if kind == "file":
        result.update(size=details.st_size, sha256=_sha256(path))
    elif kind == "symlink":
        result["target"] = os.readlink(path)
    return result


def inventory(payload: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(payload.rglob("*")):
        if path.name == "manifest.json":
            continue
        entries.append(_metadata(path, path.relative_to(payload)))
    return entries


def _source_details(source: Source, destination: Path) -> dict[str, Any]:
    details = source.path.lstat()
    return {
        "name": source.name,
        "source": str(source.path),
        "destination": destination.as_posix(),
        "source_mode": f"{stat.S_IMODE(details.st_mode):04o}",
        "source_uid": getattr(details, "st_uid", None),
        "source_gid": getattr(details, "st_gid", None),
    }


def _require_or_skip(source: Source, missing: list[dict[str, str]]) -> bool:
    if source.path.exists() or source.path.is_symlink():
        return True
    if source.required:
        raise BackupError(f"required backup source is absent: {source.path}")
    LOG.info("optional backup source is absent: %s", source.path)
    missing.append({"name": source.name, "source": str(source.path)})
    return False


def _git_bundle(source: Source, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    trusted_repository = f"safe.directory={source.path}"
    commands = (
        [
            "git", "-c", trusted_repository, "-C", str(source.path),
            "bundle", "create", str(destination), "--all",
        ],
        [
            "git", "-c", trusted_repository, "-C", str(source.path),
            "bundle", "verify", str(destination),
        ],
    )
    for command in commands:
        try:
            subprocess.run(command, check=True, timeout=600)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise BackupError(f"cannot create/verify git bundle for {source.path}") from exc


def _verify_restored_git_bundle(bundle: Path, scratch_parent: Path) -> None:
    """Verify a full bundle without depending on any production repository."""
    try:
        with tempfile.TemporaryDirectory(
            prefix=".bbb-bundle-verify-", dir=scratch_parent
        ) as temporary:
            repository = Path(temporary)
            subprocess.run(
                ["git", "init", "--bare", str(repository)],
                check=True,
                timeout=60,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "bundle", "verify", str(bundle)],
                check=True,
                timeout=600,
            )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"git bundle verification failed: {bundle}") from exc


def prepare_payload(staging_root: Path) -> Path:
    if staging_root.is_symlink():
        raise BackupError(f"staging root must not be a symlink: {staging_root}")
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(staging_root, 0o700)
    payload = staging_root / "payload"
    if payload.exists() or payload.is_symlink():
        cleanup_payload(staging_root, payload)
    payload.mkdir(mode=0o700)
    return payload


def cleanup_payload(staging_root: Path, payload: Path) -> None:
    root = staging_root.resolve(strict=True)
    if payload.name != "payload" or payload.parent.resolve(strict=True) != root:
        raise BackupError(f"refusing unsafe staging cleanup: {payload}")
    if payload.is_symlink():
        payload.unlink()
    elif payload.exists():
        shutil.rmtree(payload)


def stage(config: Config, *, integrity_check: bool) -> tuple[Path, dict[str, Any]]:
    payload = prepare_payload(config.staging_root)
    started = datetime.now(timezone.utc)
    manifest: dict[str, Any] = {
        "schema": 1,
        "run_id": str(uuid.uuid4()),
        "hostname": socket.gethostname(),
        "started_at": started.isoformat(),
        "sqlite_check": "integrity_check" if integrity_check else "quick_check",
        "databases": [],
        "paths": [],
        "git_bundles": [],
        "optional_sources_absent": [],
    }
    missing = manifest["optional_sources_absent"]
    try:
        for source in config.sqlite_databases:
            if not _require_or_skip(source, missing):
                continue
            if source.path.is_symlink():
                raise BackupError(
                    f"SQLite source must be its real path, not a symlink: {source.path}")
            destination = Path("databases") / f"{source.name}.db"
            pragma = backup_sqlite(
                source.path, payload / destination,
                integrity_check=integrity_check,
                timeout_seconds=config.sqlite_timeout_seconds)
            record = _source_details(source, destination)
            record["validation"] = pragma
            manifest["databases"].append(record)

        for source in config.paths:
            if not _require_or_skip(source, missing):
                continue
            destination = Path("files") / source.name / source.path.name
            copy_source(source.path, payload / destination)
            manifest["paths"].append(_source_details(source, destination))

        for source in config.git_repositories:
            if not _require_or_skip(source, missing):
                continue
            destination = Path("git") / f"{source.name}.bundle"
            _git_bundle(source, payload / destination)
            manifest["git_bundles"].append(_source_details(source, destination))

        manifest["files"] = inventory(payload)
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path = payload / "manifest.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(manifest_path, 0o600)
        return payload, manifest
    except Exception:
        cleanup_payload(config.staging_root, payload)
        raise


def run_backup(config: Config, *, integrity_check: bool) -> None:
    health = Healthcheck(os.environ.get("BBB_BACKUP_HEALTHCHECK_URL"))
    health.ping("start")
    payload: Path | None = None
    try:
        validate_runtime(config)
        payload, manifest = stage(config, integrity_check=integrity_check)
        LOG.info(
            "staged %d database(s), %d path(s), %d git bundle(s)",
            len(manifest["databases"]), len(manifest["paths"]),
            len(manifest["git_bundles"]),
        )
        restic = Restic(config)
        restic.backup(payload)
        # Copy before local expiry so an earlier failed R2 run can catch up.
        restic.copy_to_r2()
        restic.forget_local()
        restic.forget_r2()
    except Exception:
        health.ping("fail")
        raise
    else:
        health.ping("success")
    finally:
        if payload is not None and (payload.exists() or payload.is_symlink()):
            cleanup_payload(config.staging_root, payload)


def run_check(config: Config) -> None:
    health = Healthcheck(os.environ.get("BBB_BACKUP_CHECK_HEALTHCHECK_URL"))
    health.ping("start")
    try:
        validate_runtime(config)
        restic = Restic(config)
        restic.check_local()
        restic.check_r2()
    except Exception:
        health.ping("fail")
        raise
    else:
        health.ping("success")


def prepare_restore_target(target: Path) -> None:
    if not target.is_absolute():
        raise BackupError("restore target must be an absolute path")
    if target == Path(target.anchor):
        raise BackupError("refusing to restore over a filesystem root")
    if target.is_symlink():
        raise BackupError("restore target must not be a symlink")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(target.iterdir()):
        raise BackupError(f"restore target must be empty: {target}")
    os.chmod(target, 0o700)


def cleanup_restore_target(target: Path) -> None:
    if (not target.is_absolute() or target == Path(target.anchor)
            or target.is_symlink() or not target.exists()):
        raise BackupError(f"refusing unsafe restore cleanup: {target}")
    shutil.rmtree(target)


def _manifest_destination(payload: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise BackupError(f"unsafe manifest destination: {raw!r}")
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise BackupError(f"unsafe manifest destination: {raw!r}")
    return payload / relative


def verify_restore(target: Path) -> Path:
    manifests = list(target.rglob("manifest.json"))
    if len(manifests) != 1:
        raise BackupError(
            f"restored snapshot must contain exactly one manifest; found {len(manifests)}")
    manifest_path = manifests[0]
    payload = manifest_path.parent
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"cannot read restored manifest: {exc}") from exc
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
        raise BackupError("unsupported or incomplete restored manifest")

    errors: list[str] = []
    expected_paths = {str(item.get("path", "")) for item in manifest["files"]}
    actual_paths = {
        path.relative_to(payload).as_posix()
        for path in payload.rglob("*")
        if path != manifest_path
    }
    for extra in sorted(actual_paths - expected_paths):
        errors.append(f"unexpected restored path: {extra}")
    for expected in manifest["files"]:
        raw_path = expected.get("path", "")
        relative = Path(str(raw_path))
        if not isinstance(raw_path, str) or not raw_path or relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe manifest path: {relative}")
            continue
        restored = payload / relative
        if not restored.exists() and not restored.is_symlink():
            errors.append(f"missing: {relative.as_posix()}")
            continue
        actual = _metadata(restored, relative)
        for key in ("type", "mode", "uid", "gid", "size", "sha256", "target"):
            if key in expected and actual.get(key) != expected[key]:
                errors.append(
                    f"{relative.as_posix()} {key}: expected {expected[key]!r}, "
                    f"observed {actual.get(key)!r}")

    for database in manifest.get("databases", []):
        try:
            restored = _manifest_destination(payload, database["destination"])
        except (BackupError, KeyError) as exc:
            errors.append(str(exc))
            continue
        try:
            uri = restored.resolve(strict=True).as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                results = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if results != ["ok"]:
                errors.append(f"SQLite integrity_check failed: {database['destination']}")
        except sqlite3.Error as exc:
            errors.append(f"cannot check {database['destination']}: {exc}")

    for bundle in manifest.get("git_bundles", []):
        try:
            restored = _manifest_destination(payload, bundle["destination"])
        except (BackupError, KeyError) as exc:
            errors.append(str(exc))
            continue
        try:
            _verify_restored_git_bundle(restored, target)
        except BackupError:
            errors.append(f"git bundle verification failed: {bundle['destination']}")

    if errors:
        summary = "\n- ".join(errors[:20])
        raise BackupError(f"restored snapshot verification failed:\n- {summary}")
    LOG.info("verified restored manifest, files, databases and git bundles")
    return payload


def run_restore(config: Config, repository: str, target: Path) -> None:
    using_local = repository == "local"
    validate_runtime(
        config,
        require_local_mount=using_local,
        require_local_password=using_local,
        require_r2_credentials=not using_local,
    )
    prepare_restore_target(target)
    try:
        Restic(config).restore(repository, target)
        verify_restore(target)
    except Exception:
        cleanup_restore_target(target)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=Path(os.environ.get("BBB_BACKUP_CONFIG", DEFAULT_CONFIG)),
        help=f"configuration file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="create local and R2 snapshots")
    backup.add_argument(
        "--integrity-check", action="store_true",
        help="use full SQLite integrity_check instead of nightly quick_check",
    )
    subparsers.add_parser("check", help="run restic check against both repositories")
    initialise = subparsers.add_parser("init", help="explicitly initialise repositories")
    initialise.add_argument("destination", choices=("local", "r2", "both"))
    restore = subparsers.add_parser(
        "restore", help="restore latest snapshot to an empty scratch directory and verify it")
    restore.add_argument("repository", choices=("local", "r2"))
    restore.add_argument("target", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = Config.load(args.config)
        with ProcessLock(config.lock_file):
            if args.command == "backup":
                run_backup(config, integrity_check=args.integrity_check)
            elif args.command == "check":
                run_check(config)
            elif args.command == "restore":
                run_restore(config, args.repository, args.target)
            else:
                validate_runtime(
                    config,
                    # R2 is initialised from local so both share chunker
                    # parameters and copied snapshots deduplicate correctly.
                    require_local_mount=True,
                    require_local_password=True,
                    require_r2_credentials=args.destination in ("r2", "both"),
                )
                restic = Restic(config)
                if args.destination in ("local", "both"):
                    restic.init_local()
                if args.destination in ("r2", "both"):
                    restic.init_r2()
        return 0
    except AlreadyRunning as exc:
        # A coincident timer is harmless; the lock holder owns the health ping.
        LOG.info("%s; exiting without starting a duplicate", exc)
        return 0
    except BackupError as exc:
        LOG.error("backup failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
