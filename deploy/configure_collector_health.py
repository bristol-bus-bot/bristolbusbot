#!/usr/bin/env python3
"""Securely add the collector Healthchecks URL to the Pi-owned .env file."""
from __future__ import annotations

import getpass
import os
import stat
import tempfile
import urllib.parse
from pathlib import Path


TARGET = Path(os.environ.get(
    "BBB_COLLECTOR_ENV", Path.home() / "bbb-collector/.env"))
KEY = "BBB_COLLECTOR_HEALTHCHECK_URL"


def validate_healthcheck_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parts = urllib.parse.urlsplit(candidate)
    path_parts = [item for item in parts.path.split("/") if item]
    if (
        parts.scheme != "https"
        or parts.hostname != "hc-ping.com"
        or parts.username is not None
        or parts.password is not None
        or len(path_parts) != 1
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "use the base ping URL https://hc-ping.com/CHECK_UUID"
        )
    return candidate


def update_env_text(text: str, url: str) -> str:
    replacement = f"{KEY}={url}"
    output: list[str] = []
    replaced = False
    for line in text.splitlines():
        if line.startswith(f"{KEY}="):
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(replacement)
    return "\n".join(output) + "\n"


def write_private_atomic(path: Path, text: str) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        raise RuntimeError(f"refusing non-regular environment file: {path}")
    if hasattr(os, "geteuid") and details.st_uid != os.geteuid():
        raise RuntimeError(f"environment file is not owned by the current user: {path}")
    if os.name != "nt" and details.st_mode & 0o077:
        raise RuntimeError(f"environment file must be mode 0600: {path}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    try:
        existing = TARGET.read_text(encoding="utf-8")
        url = validate_healthcheck_url(
            getpass.getpass("Collector Healthchecks base ping URL (hidden): "))
        write_private_atomic(TARGET, update_env_text(existing, url))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Collector Healthchecks configuration failed: {exc}")
        return 1
    print("Collector Healthchecks URL written securely; no secret was displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
