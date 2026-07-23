#!/usr/bin/env python3
"""Atomically promote approved editorial data and health-gate the bot restart."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from editorial_context import EditorialValidationError, validate_bytes


EDITORIAL_ROOT = Path("/var/lib/bristolbusbot-editorial")
BOT_HEALTH = "http://127.0.0.1:3010/api/health"
DEPLOY_USER = "@BBB_DEPLOY_USER@"
SHA_RE = re.compile(r"[0-9a-f]{40}")


class EditorialPromotionError(RuntimeError):
    """The candidate could not safely become the live editorial context."""


@dataclass(frozen=True)
class PromotionConfig:
    root: Path = EDITORIAL_ROOT

    @property
    def live(self) -> Path:
        return self.root / "editorial-context.json"

    @property
    def candidate(self) -> Path:
        return self.root / "incoming" / "editorial-context.json"

    @property
    def metadata(self) -> Path:
        return self.root / "incoming" / "metadata.json"

    @property
    def previous(self) -> Path:
        return self.root / "editorial-context.json.previous"

    @property
    def state(self) -> Path:
        return self.root / "state.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require_regular(path: Path, maximum: int) -> bytes:
    try:
        details = path.lstat()
        if path.is_symlink() or not path.is_file() \
                or details.st_size <= 0 or details.st_size > maximum:
            raise OSError("not a safe regular file")
        return path.read_bytes()
    except OSError as exc:
        raise EditorialPromotionError(
            f"{path.name} is missing or unsafe") from exc


def atomic_bytes(path: Path, raw: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = path.with_name(f".{path.name}.new-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if os.name == "posix" and not DEPLOY_USER.startswith("@"):
            import pwd  # pylint: disable=import-outside-toplevel
            account = pwd.getpwnam(DEPLOY_USER)
            os.chown(temporary, 0, account.pw_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    atomic_bytes(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        0o640,
    )


def read_metadata(path: Path) -> dict:
    raw = require_regular(path, 32 * 1024)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EditorialPromotionError("candidate metadata is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 \
            or value.get("repository") != "bristol-bus-bot/bristolbusbot" \
            or value.get("branch") != "main" \
            or value.get("path") != "bot/data/editorial-context.json" \
            or not SHA_RE.fullmatch(str(value.get("blob_sha", ""))) \
            or not isinstance(value.get("content"), dict):
        raise EditorialPromotionError("candidate metadata identity is invalid")
    return value


def restart_bot() -> None:
    result = subprocess.run(
        ["systemctl", "restart", "bbb-bot.service"],
        check=False, capture_output=True, text=True)
    if result.returncode:
        raise EditorialPromotionError(
            f"bot restart failed: {result.stderr.strip()[:200]}")


def bot_has_digest(expected: str) -> bool:
    try:
        with urllib.request.urlopen(BOT_HEALTH, timeout=5) as response:
            payload = json.load(response)
        editorial = payload["details"]["healthData"]["application"]["editorialContext"]
        return payload.get("success") is True \
            and payload.get("runtime") == "systemd" \
            and editorial.get("loaded") is True \
            and editorial.get("sha256") == expected
    except (OSError, KeyError, TypeError, ValueError):
        return False


def wait_healthy(expected: str, attempts: int = 30) -> bool:
    for _ in range(attempts):
        if bot_has_digest(expected):
            return True
        time.sleep(2)
    return False


def cleanup_candidate(config: PromotionConfig) -> None:
    config.candidate.unlink(missing_ok=True)
    config.metadata.unlink(missing_ok=True)


def promote(
    config: PromotionConfig,
    *,
    restart: Callable[[], None] = restart_bot,
    healthy: Callable[[str], bool] = wait_healthy,
) -> tuple[int, dict]:
    if config.root.is_symlink():
        raise EditorialPromotionError("editorial root cannot be a symlink")
    started = time.monotonic()
    candidate_raw = require_regular(config.candidate, 256 * 1024)
    metadata = read_metadata(config.metadata)
    try:
        _, summary = validate_bytes(candidate_raw)
    except EditorialValidationError as exc:
        raise EditorialPromotionError(
            f"candidate failed promotion validation: {exc}") from exc
    if metadata["content"] != summary:
        raise EditorialPromotionError(
            "candidate content differs from its fetch metadata")

    live_raw = require_regular(config.live, 256 * 1024)
    live_digest = digest(live_raw)
    record = {
        "schema_version": 1,
        "started_at": utcnow(),
        "outcome": "running",
        "blob_sha": metadata["blob_sha"],
        "content": summary,
        "previous_sha256": live_digest,
    }
    atomic_json(config.state, record)
    if live_digest == summary["sha256"]:
        cleanup_candidate(config)
        record.update({
            "outcome": "no_change",
            "finished_at": utcnow(),
            "duration_seconds": round(time.monotonic() - started, 3),
        })
        atomic_json(config.state, record)
        return 75, record

    atomic_bytes(config.previous, live_raw, 0o640)
    changed = False
    try:
        atomic_bytes(config.live, candidate_raw, 0o640)
        changed = True
        restart()
        if not healthy(summary["sha256"]):
            raise EditorialPromotionError(
                "bot did not report the promoted editorial digest")
    except Exception as exc:
        recovery_healthy = False
        if changed:
            atomic_bytes(config.live, live_raw, 0o640)
            try:
                restart()
                recovery_healthy = healthy(live_digest)
            except Exception:
                recovery_healthy = False
        record.update({
            "outcome": "rolled_back" if recovery_healthy else "rollback_failed",
            "finished_at": utcnow(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": str(exc)[:500],
            "recovery_healthy": recovery_healthy,
        })
        atomic_json(config.state, record)
        cleanup_candidate(config)
        if recovery_healthy:
            raise EditorialPromotionError(
                "candidate was rolled back after its health gate failed") from exc
        raise EditorialPromotionError(
            "editorial promotion and rollback health gate both failed") from exc

    cleanup_candidate(config)
    record.update({
        "outcome": "accepted",
        "finished_at": utcnow(),
        "duration_seconds": round(time.monotonic() - started, 3),
    })
    atomic_json(config.state, record)
    return 0, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=EDITORIAL_ROOT)
    args = parser.parse_args(argv)
    code, record = promote(PromotionConfig(args.root))
    print(json.dumps({
        "status": record["outcome"],
        "blob_sha": record["blob_sha"],
        **record["content"],
    }, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
