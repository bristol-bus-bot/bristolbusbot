#!/usr/bin/env python3
"""One safe deployment command for every BristolBusBot production component."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, TextIO

from local_config import DeploySettings, LocalConfigError, load_deploy_settings


REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
MARKER = "/etc/bristolbusbot/unified-deploy-layout"
DURABLE_ENRICHMENT_DIRECTORY = PurePosixPath(
    "/var/lib/bristolbusbot/enrichment")
DURABLE_FLEET_FILE = DURABLE_ENRICHMENT_DIRECTORY / "fbribuses.json"
BOT_ENRICHMENT_FILES = (
    "fbribuses.json",
    "stop_localities.json",
    "stop_enrichment.json",
    "local_flavour.json",
    "route_details.json",
)
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
CORE_CODE_COMPONENTS = ("pipeline", "collector", "site", "bot")
CODE_COMPONENTS = (*CORE_CODE_COMPONENTS, "social")
# --all intentionally remains the live core stack. Social has separate
# attended gates and must always be deployed explicitly.
ALL_COMPONENTS = (*CORE_CODE_COMPONENTS, "tunnel")
CHOICES = (*ALL_COMPONENTS, "social")
FORBIDDEN_NAMES = {
    ".env", "live.db", "audit.db", "app_data.db", "timetable.db",
}
SKIP_PARTS = {
    ".git", ".pytest_cache", "__pycache__", "node_modules", "venv",
    ".venv", "tests", "_legacy",
}
SSH_CONNECT_TIMEOUT_SECONDS = 15
SSH_PREFLIGHT_TIMEOUT_SECONDS = 20
SSH_PREFLIGHT_ATTEMPTS = 2
REMOTE_COMMAND_TIMEOUT_SECONDS = 15 * 60
UPLOAD_TIMEOUT_SECONDS = 30 * 60
PROCESS_STOP_TIMEOUT_SECONDS = 5

log = logging.getLogger("bbb-push")


@dataclass(frozen=True)
class BuiltRelease:
    component: str
    release: str
    archive: Path
    sha256: str


class BoundedProcessTimeout(RuntimeError):
    """A child command exceeded its wall-clock limit and was stopped."""

    def __init__(self, command: list[str], timeout: float,
                 stdout: str, stderr: str) -> None:
        self.command = command
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"command timed out after {timeout:g} seconds")


def _captured_text(stream: TextIO | None) -> str:
    if stream is None:
        return ""
    stream.flush()
    stream.seek(0)
    return stream.read()


class _WindowsJob:
    """An exact Windows child tree that can be terminated as one unit."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p]
        kernel32.TerminateJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_uint]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        process_handle = ctypes.c_void_p(int(process._handle))  # type: ignore[attr-defined]
        if not kernel32.AssignProcessToJobObject(handle, process_handle):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            self._handle = None
            raise ctypes.WinError(error)

    def terminate(self) -> None:
        if self._handle is not None and not self._kernel32.TerminateJobObject(
                self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _stop_process_tree(process: subprocess.Popen[str],
                       windows_job: _WindowsJob | None = None) -> None:
    """Stop only the private process group/tree created for one command."""
    if os.name == "nt":
        if windows_job is None:
            raise RuntimeError("timed-out Windows process has no private job")
        windows_job.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as final_exc:
            raise RuntimeError(
                f"could not stop timed-out child process {process.pid}"
            ) from final_exc
        raise RuntimeError(
            f"timed-out child process tree {process.pid} needed fallback cleanup"
        ) from exc


def run_bounded_process(command: list[str], *, timeout: float,
                        capture_output: bool = True
                        ) -> subprocess.CompletedProcess[str]:
    """Run without PIPEs so inherited output handles cannot block cleanup."""
    if timeout <= 0:
        raise ValueError("process timeout must be positive")
    with ExitStack() as stack:
        stdout_stream = None
        stderr_stream = None
        if capture_output:
            stdout_stream = stack.enter_context(tempfile.TemporaryFile(
                mode="w+", encoding="utf-8", errors="replace"))
            stderr_stream = stack.enter_context(tempfile.TemporaryFile(
                mode="w+", encoding="utf-8", errors="replace"))
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        windows_job = None
        if os.name == "nt":
            try:
                windows_job = _WindowsJob(process)
            except OSError:
                process.kill()
                process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
                raise
        try:
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _stop_process_tree(process, windows_job)
                stdout = _captured_text(stdout_stream)
                stderr = _captured_text(stderr_stream)
                raise BoundedProcessTimeout(
                    command, timeout, stdout, stderr) from exc
            stdout = _captured_text(stdout_stream)
            stderr = _captured_text(stderr_stream)
            return subprocess.CompletedProcess(
                command, returncode, stdout=stdout, stderr=stderr)
        finally:
            if windows_job is not None:
                windows_job.close()


def q(value: str | PurePosixPath) -> str:
    return shlex.quote(str(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_short_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"], cwd=REPO,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip().lower()


def require_clean_tree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO,
        capture_output=True, text=True, check=True,
    )
    if result.stdout.strip():
        raise RuntimeError(
            "working tree is not clean; commit the reviewed files before a "
            "production deploy so every release maps to one Git commit"
        )


def release_id() -> str:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dt%H%M%S") + f"{now.microsecond:06d}z"
    value = f"{stamp}-{git_short_sha()}"
    if not SAFE_ID.fullmatch(value):
        raise RuntimeError("could not create a safe release identifier")
    return value


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def test_python() -> str:
    candidate = REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    return str(candidate) if candidate.exists() else sys.executable


def run_local(command: list[str], *, cwd: Path = REPO,
              env: dict[str, str] | None = None) -> None:
    log.info("local gate: %s", " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"local command failed: {' '.join(command)}\n"
            f"{result.stdout}\n{result.stderr}")


def run_gates(component: str) -> None:
    python = test_python()
    with tempfile.TemporaryDirectory(prefix=".bbb-pytest-", dir=REPO) as test_temp:
        pytest_env = {
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "TEMP": test_temp,
            "TMP": test_temp,
        }
        pytest = [python, "-m", "pytest", "-p", "no:cacheprovider",
                  "--basetemp", test_temp]
        if component == "collector":
            run_local([*pytest, "collector/tests"], env=pytest_env)
        elif component == "site":
            run_local([*pytest, "site/tests"], env=pytest_env)
            run_local([npm_command(), "test"], cwd=REPO / "site")
        elif component == "bot":
            run_local([npm_command(), "ci", "--no-audit", "--no-fund"], cwd=REPO / "bot")
            run_local([npm_command(), "run", "typecheck"], cwd=REPO / "bot")
            run_local([npm_command(), "test"], cwd=REPO / "bot")
        elif component == "pipeline":
            run_local([*pytest, "pipeline/tests"], env=pytest_env)
        elif component == "social":
            run_local([*pytest, "social/tests"], env=pytest_env)
            run_local(
                [npm_command(), "ci", "--no-audit", "--no-fund"],
                cwd=REPO / "social")
            run_local([npm_command(), "test"], cwd=REPO / "social")
        else:
            raise RuntimeError(f"no local gate is defined for {component}")


def should_skip(path: Path) -> bool:
    return (
        any(part in SKIP_PARTS for part in path.parts)
        or path.name in FORBIDDEN_NAMES
        or path.suffix in {".pyc", ".log", ".db"}
    )


def copy_file(source: Path, destination: Path) -> None:
    if should_skip(source):
        raise RuntimeError(f"refusing forbidden release input: {source}")
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"release input must be a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"release input must be a real directory: {source}")
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if should_skip(relative):
            continue
        if path.is_dir():
            continue
        copy_file(path, destination / relative)


def populate_release(component: str, root: Path) -> None:
    if component == "collector":
        copy_file(REPO / "collector/pyproject.toml", root / "pyproject.toml")
        copy_tree(REPO / "collector/src", root / "src")
        copy_file(REPO / "pipeline/check_collector_freshness.py",
                  root / "check_collector_freshness.py")
        copy_file(REPO / "pipeline/compare_collectors.py",
                  root / "compare_collectors.py")
        copy_file(REPO / "pipeline/status_digest.py", root / "status_digest.py")
    elif component == "site":
        for name in ("pyproject.toml", "wsgi.py"):
            copy_file(REPO / "site" / name, root / name)
        for name in ("app", "templates", "static"):
            copy_tree(REPO / "site" / name, root / name)
        for pattern in ("*.json", "*.geojson"):
            for path in (REPO / "site").glob(pattern):
                copy_file(path, root / path.name)
        copy_file(REPO / "collector/pyproject.toml", root / "_collector/pyproject.toml")
        copy_tree(REPO / "collector/src", root / "_collector/src")
    elif component == "bot":
        if not (REPO / "bot/dist/index.js").is_file():
            raise RuntimeError("bot build did not produce dist/index.js")
        copy_tree(REPO / "bot/dist", root / "dist")
        for name in ("package.json", "package-lock.json"):
            copy_file(REPO / "bot" / name, root / name)
    elif component == "social":
        for name in ("package.json", "package-lock.json"):
            copy_file(REPO / "social" / name, root / name)
        for pattern in ("*.py", "*.mjs"):
            for path in (REPO / "social").glob(pattern):
                copy_file(path, root / path.name)
        copy_tree(REPO / "social/fonts", root / "fonts")
        copy_tree(REPO / "social/examples", root / "examples")
    elif component == "pipeline":
        for pattern in ("*.py", "*.json", "*.geojson", "requirements-runtime.txt"):
            for path in (REPO / "pipeline").glob(pattern):
                if path.name == "fbribuses.json":
                    continue
                copy_file(path, root / path.name)
        # Geography is an audited, versioned input.  Pin the site's canonical
        # copy into the pipeline release so the networkless rollup cannot
        # silently depend on another component's live symlink.
        copy_file(REPO / "site/stop_localities.json",
                  root / "stop_localities.json")
        copy_tree(REPO / "audit-site", root / "audit_site_assets")
        copy_file(REPO / "LICENSE", root / "LICENSE")
        copy_file(REPO / "audit-site/AUDIT_METHODOLOGY.md",
                  root / "AUDIT_METHODOLOGY.md")
        copy_file(DEPLOY / "publish_to_github.sh", root / "publish_to_github.sh")
    else:
        raise RuntimeError(f"cannot build release for {component}")


def build_release(component: str, workspace: Path,
                  *, release: str | None = None) -> BuiltRelease:
    release = release or release_id()
    if not SAFE_ID.fullmatch(component) or not SAFE_ID.fullmatch(release):
        raise RuntimeError("unsafe component or release identifier")
    root = workspace / f"{component}-{release}"
    root.mkdir()
    populate_release(component, root)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"release contains a symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if path.name in FORBIDDEN_NAMES:
                raise RuntimeError(f"release contains forbidden file: {relative}")
            files[relative] = sha256_file(path)
    if not files:
        raise RuntimeError(f"{component} release is empty")
    manifest = {
        "schema": 1,
        "component": component,
        "release": release,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_short_sha(),
        "files": files,
    }
    (root / "release.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    archive = workspace / f"{component}-{release}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(root.rglob("*")):
            tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return BuiltRelease(component, release, archive, sha256_file(archive))


class Remote:
    def __init__(
        self,
        settings: DeploySettings,
        *,
        preflight_timeout: float = SSH_PREFLIGHT_TIMEOUT_SECONDS,
        command_timeout: float = REMOTE_COMMAND_TIMEOUT_SECONDS,
        upload_timeout: float = UPLOAD_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self.host = settings.host
        self.user = settings.user
        self.target = f"{settings.user}@{settings.host}"
        self.preflight_timeout = preflight_timeout
        self.command_timeout = command_timeout
        self.upload_timeout = upload_timeout
        self.options = [
            "-o", "StrictHostKeyChecking=yes",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
            "-o", "StdinNull=yes",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
        ]

    def __enter__(self) -> "Remote":
        command = [
            "ssh", *self.options, self.target,
            "/bin/sh -c 'exec </dev/null >/dev/null 2>&1; true'",
        ]
        for attempt in range(SSH_PREFLIGHT_ATTEMPTS):
            try:
                result = run_bounded_process(
                    command, timeout=self.preflight_timeout)
            except BoundedProcessTimeout as exc:
                if attempt + 1 < SSH_PREFLIGHT_ATTEMPTS:
                    log.warning(
                        "SSH preflight did not close within %g seconds; its "
                        "private process tree was stopped before any upload. "
                        "Retrying once after first-login services have started.",
                        self.preflight_timeout,
                    )
                    continue
                raise RuntimeError(
                    f"SSH preflight timed out on both bounded attempts "
                    f"({self.preflight_timeout:g} seconds each). Nothing was "
                    "uploaded and no release was switched. The deployer "
                    "stopped only its own SSH process trees. Check the Pi and "
                    "the recovery steps in deploy/README.md, then retry the "
                    "unchanged command."
                ) from exc
            if result.returncode:
                raise RuntimeError(
                    f"SSH refused the connection or host key. Connect once with ssh "
                    f"{self.target}, verify the fingerprint, then retry.\n"
                    f"{result.stderr}"
                )
            return self
        raise AssertionError("SSH preflight attempts were not executed")

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, command: str, *, check: bool = True) -> str:
        try:
            result = run_bounded_process(
                ["ssh", *self.options, self.target, command],
                timeout=self.command_timeout,
            )
        except BoundedProcessTimeout as exc:
            output = exc.stdout + exc.stderr
            raise RuntimeError(
                f"remote command timed out after "
                f"{self.command_timeout:g} seconds; its private SSH process "
                f"tree was stopped and the deployment will not continue: "
                f"{command}\n{output}"
            ) from exc
        if check and result.returncode:
            raise RuntimeError(
                f"remote command failed ({result.returncode}): {command}\n"
                f"{result.stdout}{result.stderr}"
            )
        return result.stdout + result.stderr

    def upload(self, source: Path, destination: PurePosixPath,
               *, progress: bool = False) -> None:
        if progress:
            log.info("uploading %.1f MiB; scp will report transfer progress",
                     source.stat().st_size / 1024 / 1024)
        try:
            result = run_bounded_process(
                ["scp", *self.options, str(source),
                 f"{self.target}:{destination}"],
                timeout=self.upload_timeout,
                capture_output=not progress,
            )
        except BoundedProcessTimeout as exc:
            raise RuntimeError(
                f"upload timed out after {self.upload_timeout:g} seconds; "
                "its private scp process tree was stopped and the deployer "
                "did not continue to a release switch"
            ) from exc
        if result.returncode:
            raise RuntimeError(
                f"scp failed for {source.name}\n"
                f"{result.stdout}{result.stderr}")


def notify(remote: Remote, message: str) -> None:
    script = remote.settings.notify_script
    remote.run(
        f"if test -x {q(script)}; then "
        f"{q(script)} {q(message)} >/dev/null 2>&1 || true; fi",
        check=False,
    )


def setup_command(component: str, release_dir: PurePosixPath) -> str:
    cd = f"cd {q(release_dir)}"
    if component == "collector":
        return f"{cd} && python3 -m venv venv && venv/bin/pip install -q ."
    if component == "site":
        return (
            f"{cd} && python3 -m venv venv && "
            "venv/bin/pip install -q ./_collector . gunicorn"
        )
    if component == "bot":
        return f"{cd} && npm ci --omit=dev --no-audit --no-fund"
    if component == "social":
        return f"{cd} && npm ci --omit=dev --no-audit --no-fund"
    if component == "pipeline":
        return (
            f"{cd} && python3 -m venv venv && "
            "venv/bin/pip install -q -r requirements-runtime.txt && "
            "venv/bin/python3 -c 'from audit_geo import load_geo_index; "
            "load_geo_index()' && "
            f"BBB_FLEET_FILE={q(DURABLE_FLEET_FILE)} "
            "venv/bin/python3 -c 'from audit_fleet import load_fleet_index; "
            "load_fleet_index()' && "
            "chmod 0755 publish_to_github.sh"
        )
    raise RuntimeError(f"no setup command for {component}")


def atomic_switch(remote: Remote, component: str, target: str) -> None:
    current = remote.settings.remote_base / "current" / component
    temporary = remote.settings.remote_base / "current" / f".{component}.next"
    remote.run(
        f"rm -f {q(temporary)} && ln -s {q(target)} {q(temporary)} && "
        f"mv -Tf {q(temporary)} {q(current)}"
    )


def healthy(remote: Remote, component: str) -> bool:
    pipeline = remote.settings.remote_base / "current" / "pipeline"
    social = remote.settings.remote_base / "current" / "social"
    commands = {
        "collector": (
            "systemctl is-active --quiet bbb-collector.service && "
            "/usr/local/libexec/bbb-verify-collector-state --max-poll-age 180 >/dev/null"
        ),
        "site": (
            "systemctl is-active --quiet bbb-site.service && "
            "python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("
            "\"http://127.0.0.1:5002/healthz\",timeout=10)); assert d.get(\"status\") "
            "in (\"ok\",\"warn\")'"
        ),
        "bot": (
            "/usr/local/libexec/bbb-enrichment-layout validate --quiet && "
            "systemctl is-active --quiet bbb-bot.service && "
            "python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("
            "\"http://127.0.0.1:3010/api/health\",timeout=10)); h=d[\"details\"][\"healthData\"]; "
            "e=h[\"application\"][\"enrichmentData\"]; "
            "assert d.get(\"success\") is True and d.get(\"runtime\")==\"systemd\" "
            "and h[\"database\"][\"timetable\"][\"connected\"] is True "
            "and h[\"database\"][\"appData\"][\"connected\"] is True "
            "and e[\"fleet\"][\"loaded\"] is True "
            "and e[\"fleet\"][\"records\"] == "
            "h[\"application\"][\"state\"][\"busDetailsLoaded\"] "
            "and e[\"localities\"][\"loaded\"] is True "
            "and e[\"localities\"][\"records\"] > 0'"
        ),
        "pipeline": (
            f"test -x {q(pipeline / 'venv/bin/python3')} && "
            f"cd {q(pipeline)} && "
            f"{q(pipeline / 'venv/bin/python3')} -m py_compile "
            f"{q(pipeline / 'audit_snapshot.py')} "
            f"{q(pipeline / 'audit_rollup.py')} "
            f"{q(pipeline / 'audit_export.py')} "
            f"{q(pipeline / 'audit_integration.py')} "
            f"{q(pipeline / 'audit_promote.py')} && "
            f"BBB_FLEET_FILE={q(DURABLE_FLEET_FILE)} "
            f"{q(pipeline / 'venv/bin/python3')} -c "
            "'from audit_fleet import load_fleet_index; load_fleet_index()' && "
            "systemctl is-active --quiet bbb-audit-snapshot.timer "
            "bbb-audit-rollup.timer bbb-audit-publish.timer"
        ),
        "tunnel": (
            "systemctl is-active --quiet bbb-tunnel.service && "
            "curl -fsS --max-time 20 https://bristolbuses.live/healthz >/dev/null"
        ),
        "social": (
            "set -eu; output=$(mktemp -d /tmp/bbb-social-health.XXXXXX); "
            "trap 'rm -rf -- \"$output\"' EXIT INT TERM; "
            f"cd {q(social)}; "
            "node generate-pack.mjs --input examples/demo-pack.json "
            "--output \"$output\" --card bot-said >/dev/null; "
            "file \"$output/01-the-bot-said.jpg\" | "
            "grep -q 'JPEG image data.*1080x1350'"
        ),
    }
    return remote.run(commands[component], check=False).strip() == ""


def wait_healthy(remote: Remote, component: str, attempts: int = 12) -> bool:
    for attempt in range(attempts):
        if healthy(remote, component):
            return True
        if attempt + 1 < attempts:
            time.sleep(5 if component == "collector" else 2)
    return False


def restart(remote: Remote, component: str) -> None:
    if component in {"pipeline", "social"}:
        return
    remote.run(f"sudo -n /usr/local/sbin/bbb-deploy-control restart {component}")


def deploy_release(remote: Remote, built: BuiltRelease, *,
                   notify_success: bool = True) -> None:
    component = built.component
    remote_base = remote.settings.remote_base
    release_dir = remote_base / "releases" / component / built.release
    incoming = remote_base / "incoming" / built.archive.name
    previous = remote.run(
        f"test -L {q(remote_base / 'current' / component)} && "
        f"readlink -f {q(remote_base / 'current' / component)}",
        check=False,
    ).strip()
    switched = False
    try:
        remote.run(f"test -f {MARKER}")
        if component != "social":
            remote.run(f"/usr/local/libexec/bbb-validate-config {component}")
        remote.run(
            f"mkdir -p {q(release_dir.parent)} {q(remote_base / 'incoming')} && "
            f"test ! -e {q(release_dir)} && mkdir {q(release_dir)}"
        )
        remote.upload(built.archive, PurePosixPath(f"{incoming}.part"))
        remote.run(
            f"test \"$(sha256sum {q(str(incoming) + '.part')} | cut -d' ' -f1)\" = "
            f"{q(built.sha256)} && mv -f {q(str(incoming) + '.part')} {q(incoming)} && "
            f"tar --no-same-owner --no-same-permissions -xzf {q(incoming)} -C {q(release_dir)} && "
            f"rm -f {q(incoming)}"
        )
        remote.run(
            f"/usr/local/libexec/bbb-verify-release {q(release_dir)} "
            f"{q(component)} {q(built.release)}"
        )
        remote.run(setup_command(component, release_dir))
        atomic_switch(remote, component, str(release_dir))
        switched = True
        restart(remote, component)
        if not wait_healthy(remote, component):
            raise RuntimeError(f"{component} failed its production health gate")
    except Exception:
        if switched:
            if previous:
                log.error("%s failed health; restoring %s", component, previous)
                atomic_switch(remote, component, previous)
                restart(remote, component)
                if not wait_healthy(remote, component):
                    notify(remote, f":rotating_light: BristolBusBot {component} deploy and rollback both failed")
                    raise RuntimeError(
                        f"CRITICAL: {component} rollback did not recover health"
                    )
            elif component == "social":
                current = remote.settings.remote_base / "current" / "social"
                remote.run(f"test -L {q(current)} && rm -f {q(current)}")
            else:
                raise RuntimeError(
                    f"CRITICAL: {component} had no previous release to restore")
        notify(remote, f":warning: BristolBusBot {component} deploy failed; previous release retained")
        raise
    if notify_success:
        notify(remote, f":white_check_mark: BristolBusBot {component} deployed {built.release}")
    log.info("%s deployed and healthy (%s)", component, built.release)


def render_template(source: Path, destination: Path,
                    settings: DeploySettings) -> None:
    text = source.read_text(encoding="utf-8")
    for token, value in settings.render_tokens().items():
        text = text.replace(token, value)
    unresolved = sorted(set(re.findall(r"@BBB_[A-Z0-9_]+@", text)))
    if unresolved:
        raise RuntimeError(
            f"unresolved deployment template values in {source}: "
            + ", ".join(unresolved)
        )
    destination.write_text(text, encoding="utf-8", newline="\n")


def deploy_tunnel(remote: Remote, workspace: Path, *,
                  notify_success: bool = True) -> None:
    template = DEPLOY / "cloudflared/config.yml"
    source = workspace / "cloudflared-config.yml"
    render_template(template, source, remote.settings)
    if not source.is_file():
        raise RuntimeError("canonical tunnel config is missing")
    remote.run(f"test -f {MARKER} && /usr/local/libexec/bbb-validate-config tunnel")
    destination = remote.settings.remote_base / "incoming" / "tunnel-config.yml"
    remote.upload(source, PurePosixPath(f"{destination}.part"))
    remote.run(f"mv -f {q(str(destination) + '.part')} {q(destination)}")
    try:
        remote.run("sudo -n /usr/local/sbin/bbb-deploy-control tunnel-promote")
        if not wait_healthy(remote, "tunnel"):
            raise RuntimeError("tunnel failed its public health gate")
    except Exception:
        remote.run("sudo -n /usr/local/sbin/bbb-deploy-control tunnel-rollback", check=False)
        if not wait_healthy(remote, "tunnel"):
            notify(remote, ":rotating_light: BristolBusBot tunnel deploy and rollback both failed")
            raise RuntimeError("CRITICAL: tunnel rollback did not recover public health")
        notify(remote, ":warning: BristolBusBot tunnel deploy failed; previous config restored")
        raise
    if notify_success:
        notify(remote, ":white_check_mark: BristolBusBot tunnel config deployed")
    log.info("tunnel config deployed and public health is good")


def validate_local_timetable(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"timetable is not a regular file: {path}")
    sys.path.insert(0, str(DEPLOY))
    from timetable_control import validate  # pylint: disable=import-outside-toplevel
    result = validate(path)
    log.info("local timetable valid: %s", result)


def deploy_timetable(remote: Remote, source: Path) -> None:
    validate_local_timetable(source)
    remote.run(f"test -f {MARKER} && /usr/local/libexec/bbb-validate-config pipeline")
    upload = PurePosixPath("/var/lib/bristolbusbot/pipeline/.timetable.db.upload")
    remote.upload(source, PurePosixPath(f"{upload}.part"), progress=True)
    digest = sha256_file(source)
    remote.run(
        f"test \"$(sha256sum {q(str(upload) + '.part')} | cut -d' ' -f1)\" = {q(digest)} && "
        f"mv -f {q(str(upload) + '.part')} {q(upload)}"
    )
    promoted = False
    try:
        remote.run("sudo -n /usr/local/sbin/bbb-deploy-control timetable-promote")
        promoted = True
        for component in ("collector", "site", "bot"):
            restart(remote, component)
            if not wait_healthy(remote, component):
                raise RuntimeError(f"{component} rejected the new timetable")
    except Exception:
        if promoted:
            remote.run("sudo -n /usr/local/sbin/bbb-deploy-control timetable-rollback")
            for component in ("collector", "site", "bot"):
                restart(remote, component)
            recovered = all(wait_healthy(remote, name) for name in ("collector", "site", "bot"))
            if not recovered:
                notify(remote, ":rotating_light: BristolBusBot timetable rollback did not recover all consumers")
                raise RuntimeError("CRITICAL: timetable rollback did not recover all consumers")
        notify(remote, ":warning: BristolBusBot timetable rejected; previous database restored")
        raise
    log.info("timetable deployed; collector, site and bot are healthy")


def install_payload(workspace: Path, settings: DeploySettings) -> Path:
    root = workspace / "unified-layout"
    root.mkdir()
    for name in (
        "install_unified_deploy.sh", "deploy_control.sh", "timetable_control.py",
        "timetable_delivery.py", "timetable_promote.py",
        "timetable_service_profile.py",
        "validate_production_config.py", "verify_release.py",
        "verify_collector_state.py", "run_audit_rollup.sh", "publish_to_github.sh",
        "run_recorded_job.py", "aggregate_health.py", "sample_resources.py",
        "data_health.py", "collector_anomaly_report.py",
        "audit_origin_backfill.py",
        "configure_timetable_delivery.py", "configure_social_curation.py",
        "configure_blurb_generation.py", "blurb_automation.py",
        "blurb_review.sh",
        "enrichment_layout.py", "data_promotion.py",
        "enrichment_contracts.py", "enrichment_promote.py",
        "fleet_candidate_stage.py", "locality_candidate_stage.py",
        "editorial_context.py", "editorial_fetch.py", "editorial_promote.py",
    ):
        copy_file(DEPLOY / name, root / name)
    copy_file(REPO / "bot/data/editorial-context.json",
              root / "editorial-context.json")
    copy_file(REPO / "pipeline/model-context.json",
              root / "model-context.json")
    copy_file(REPO / "pipeline/timetable_manifest.py", root / "timetable_manifest.py")
    copy_file(REPO / "pipeline/timetable_editions.py", root / "timetable_editions.py")
    copy_file(REPO / "pipeline/update_fleet_data.py", root / "update_fleet_data.py")
    copy_file(REPO / "pipeline/geocode_stops.py", root / "geocode_stops.py")
    copy_tree(DEPLOY / "systemd", root / "systemd")
    copy_tree(DEPLOY / "sudoers", root / "sudoers")
    copy_tree(DEPLOY / "tmpfiles", root / "tmpfiles")
    for path in root.rglob("*"):
        if path.is_file():
            render_template(path, path, settings)
    archive = workspace / "unified-layout.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(root.rglob("*")):
            tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return archive


def install_layout(remote: Remote, workspace: Path) -> None:
    archive = install_payload(workspace, remote.settings)
    release = release_id()
    remote_dir = PurePosixPath(f"/tmp/bbb-unified-layout-{release}")
    remote_archive = PurePosixPath(f"{remote_dir}.tar.gz")
    remote.upload(archive, PurePosixPath(f"{remote_archive}.part"))
    remote.run(
        f"mv -f {q(str(remote_archive) + '.part')} {q(remote_archive)} && "
        f"mkdir {q(remote_dir)} && tar -xzf {q(remote_archive)} -C {q(remote_dir)}"
    )
    remote.run(
        f"sudo -n /bin/sh {q(remote_dir / 'install_unified_deploy.sh')} {q(remote_dir)}"
    )
    remote.run(f"test -f {MARKER}")
    log.info("unified deployment layout installed and live health checks passed")


def refresh_timetable(no_download: bool) -> Path:
    command = [sys.executable, str(REPO / "pipeline/build_timetable.py"), "--skip-deploy"]
    if no_download:
        command.append("--no-download")
    run_local(command)
    value = os.environ.get("BBB_TIMETABLE_DB")
    return Path(value) if value else REPO / "pipeline/timetable.db"


def command_plan(args: argparse.Namespace) -> list[str]:
    if args.install_layout:
        return [
            "Install or update the exact sudo allowlist, helpers and release-aware systemd units.",
            "Create any missing current symlinks while preserving existing live release selections.",
            "Restart and health-check all four live services, restoring the old units on failure.",
        ]
    if args.timetable:
        return [
            f"Validate and upload timetable: {args.timetable}",
            "Atomically replace only /var/lib/bristolbusbot/pipeline/timetable.db.",
            "Restart collector, site and bot; restore the previous timetable if any rejects it.",
        ]
    if args.refresh_timetable:
        return [
            "Build and validate a fresh timetable locally.",
            "Deploy that database atomically, then restart and check collector, site and bot.",
            "Do not change any application code or production secret.",
        ]
    components = list(ALL_COMPONENTS) if args.all else [args.component]
    if components == ["social"]:
        return [
            "Run the social Python/JavaScript gates and prepare one immutable release.",
            "Switch only current/social after an ARM64 1080x1350 render succeeds.",
            "Do not start the curation service, enable its timer or contact Slack.",
            "Do not overwrite credentials or durable delivery state.",
        ]
    return [
        f"Run local gates and prepare immutable releases for: {', '.join(components)}.",
        "Switch only the affected current symlink and restart only that service.",
        "Health-check after every switch and automatically restore the previous release on failure.",
        "Do not overwrite .env files, credentials or durable SQLite databases.",
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands and scope:
  --component collector  collector code + monitoring scripts; restarts collector
  --component site       website + pinned collector snapshot; restarts site
  --component bot        locally built bot + runtime JSON; restarts bot
  --component pipeline   scheduled audit job code; restarts no long-running service
  --component tunnel     public non-secret tunnel config; restarts tunnel
  --component social     isolated renderer/curation code; leaves timer disabled
  --all                  pipeline, collector, site, bot and tunnel code/config;
                         does NOT rebuild or replace the timetable database
  --timetable PATH       only a known timetable; restarts collector, site and bot
  --refresh-timetable    builds locally, then performs --timetable deployment
  --install-layout       install/update symlink, systemd and sudo layout; no app code
  --dry-run              prints the exact scope; no build, SSH or live change

Every code release is staged and verified before its atomic switch. Production
secrets stay in /etc/bristolbusbot and durable data stays in /var/lib/bristolbusbot.
""",
    )
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--component", choices=CHOICES)
    action.add_argument("--all", action="store_true")
    action.add_argument("--timetable", type=Path, metavar="PATH")
    action.add_argument("--refresh-timetable", action="store_true")
    action.add_argument("--install-layout", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--no-download", action="store_true",
                        help="with --refresh-timetable, reuse the existing local GTFS")
    result.add_argument("--host", help=argparse.SUPPRESS)
    result.add_argument("--user", help=argparse.SUPPRESS)
    result.add_argument("--remote-home", help=argparse.SUPPRESS)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.no_download and not args.refresh_timetable:
        raise SystemExit("--no-download is valid only with --refresh-timetable")
    for line in command_plan(args):
        print(f"- {line}")
    if args.dry_run:
        print("DRY RUN COMPLETE: no build, SSH connection or live change was made.")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        require_clean_tree()
        settings = load_deploy_settings().with_overrides(
            user=args.user, host=args.host, remote_home=args.remote_home)
        with tempfile.TemporaryDirectory(prefix=".bbb-push-", dir=REPO) as temp:
            workspace = Path(temp)
            if args.install_layout:
                with Remote(settings) as remote:
                    install_layout(remote, workspace)
                return 0

            timetable = args.timetable
            if args.refresh_timetable:
                timetable = refresh_timetable(args.no_download)
            if timetable is not None:
                validate_local_timetable(timetable)
                with Remote(settings) as remote:
                    deploy_timetable(remote, timetable)
                return 0

            components = list(ALL_COMPONENTS) if args.all else [args.component]
            built: dict[str, BuiltRelease] = {}
            common_release = release_id()
            for component in components:
                if component in CODE_COMPONENTS:
                    run_gates(component)
                    built[component] = build_release(
                        component, workspace, release=common_release)
            run_local([test_python(), str(REPO / "scripts/scan_secrets.py")])
            run_local([
                test_python(), str(REPO / "scripts/scan_public_metadata.py")])
            with Remote(settings) as remote:
                for component in components:
                    if component == "tunnel":
                        deploy_tunnel(
                            remote, workspace, notify_success=False)
                    else:
                        deploy_release(
                            remote, built[component], notify_success=False)
            return 0
    except (OSError, RuntimeError, LocalConfigError,
            subprocess.SubprocessError) as exc:
        log.error("deployment stopped safely: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
