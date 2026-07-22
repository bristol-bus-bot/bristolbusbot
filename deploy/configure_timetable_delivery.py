#!/usr/bin/env python3
"""Interactively write the root-only GitHub timetable-delivery credential."""
from __future__ import annotations

import argparse
import getpass
import os
import shlex
import stat
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path


DEFAULT_ENV = Path("/etc/bristolbusbot/timetable-delivery.env")
DEFAULT_TOKEN = Path("/etc/bristolbusbot/timetable-delivery.token")


class ConfigurationError(ValueError):
    """The requested credential configuration is incomplete or unsafe."""


def validate_token(value: str) -> str:
    token = value.strip()
    if (len(token) < 20 or len(token) > 512
            or any(character.isspace() or ord(character) < 32 for character in token)):
        raise ConfigurationError("token is empty or has an unsafe format")
    return token


def validate_expiry(value: str, *, today: date | None = None) -> str:
    try:
        expires = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ConfigurationError("expiry must use YYYY-MM-DD") from exc
    if expires <= (today or datetime.now(timezone.utc).date()):
        raise ConfigurationError("token expiry must be in the future")
    return datetime(
        expires.year, expires.month, expires.day,
        tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def render_env(expiry: str) -> str:
    return f"BBB_GITHUB_TOKEN_EXPIRES_UTC={shlex.quote(expiry)}\n"


def validate_target(path: Path, *, replace: bool) -> None:
    if path.exists() or path.is_symlink():
        if not replace:
            raise ConfigurationError(
                f"{path} already exists; use --replace only for an intentional rotation")
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or path.is_symlink():
            raise ConfigurationError("existing credential is not a regular file")


def private_write(path: Path, content: str, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    validate_target(path, replace=replace)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--env", type=Path, default=DEFAULT_ENV)
    result.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    result.add_argument("--replace", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise SystemExit("configure_timetable_delivery.py must run as root")
    try:
        if args.env.absolute() == args.token.absolute():
            raise ConfigurationError("token and expiry paths must be different")
        validate_target(args.env, replace=args.replace)
        validate_target(args.token, replace=args.replace)
        token = validate_token(getpass.getpass(
            "Fine-grained GitHub token for Actions read/write (hidden): "))
        expiry = validate_expiry(input("Token expiry date shown by GitHub (YYYY-MM-DD): "))
        private_write(args.token, token + "\n", replace=args.replace)
        private_write(args.env, render_env(expiry), replace=args.replace)
    except (OSError, ConfigurationError) as exc:
        raise SystemExit(f"configuration failed: {exc}") from exc
    print("Timetable-delivery credential written root-only; no token was displayed.")
    print("Enable bbb-timetable-shadow.timer after the reviewed layout is installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
