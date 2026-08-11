#!/usr/bin/env python3
"""Seed a least-privilege blurb environment from the existing bot AI key."""
from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path


BOT_ENV = Path("/etc/bristolbusbot/bot.env")
BLURB_ENV = Path("/etc/bristolbusbot/blurb.env")
SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]{20,200}$")


class ConfigError(RuntimeError):
    pass


def values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError("bot environment is unavailable") from exc
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in result:
            raise ConfigError(f"bot environment repeats {key}")
        value = value.strip()
        if value[:1] in {'"', "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(f"bot environment has malformed quotes for {key}")
            value = value[1:-1]
        result[key] = value
    return result


def validate_existing(path: Path, expected_gid: int) -> None:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ConfigError("existing blurb environment is unsafe")
    if os.name == "posix" and (
            info.st_uid != 0 or info.st_gid != expected_gid
            or stat.S_IMODE(info.st_mode) != 0o640):
        raise ConfigError("existing blurb environment permissions are unsafe")
    parsed = values(path)
    key = parsed.get("GEMINI_API_KEY", "")
    model = parsed.get("BLURB_GEMINI_MODEL", "")
    if not SAFE_KEY.fullmatch(key):
        raise ConfigError("existing blurb API key is malformed")
    if not SAFE_MODEL.fullmatch(model):
        raise ConfigError("existing blurb model is malformed")


def install(*, bot_env: Path, output: Path, owner: str) -> str:
    import pwd  # POSIX-only production configuration.
    try:
        account = pwd.getpwnam(owner)
    except KeyError as exc:
        raise ConfigError("deployment account does not exist") from exc
    if output.exists() or output.is_symlink():
        validate_existing(output, account.pw_gid)
        return "preserved"
    source_info = bot_env.lstat()
    if bot_env.is_symlink() or not stat.S_ISREG(source_info.st_mode):
        raise ConfigError("bot environment is unsafe")
    source = values(bot_env)
    key = source.get("AI_API_KEY", "")
    if not SAFE_KEY.fullmatch(key):
        return "not_configured"
    model = source.get("AI_MODEL", "gemini-flash-latest")
    if not SAFE_MODEL.fullmatch(model):
        raise ConfigError("bot AI model is malformed")
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join((
        f"GEMINI_API_KEY={key}",
        f"BLURB_GEMINI_MODEL={model}",
        "BLURB_MAX_IDENTITIES_PER_RUN=40",
        "BLURB_MAX_REQUESTS_PER_RUN=3",
        "BLURB_MAX_INPUT_TOKENS_PER_RUN=50000",
        "BLURB_MAX_OUTPUT_TOKENS_PER_RUN=12000",
        "BLURB_MAX_REQUESTS_PER_MONTH=18",
        "BLURB_MAX_INPUT_TOKENS_PER_MONTH=300000",
        "BLURB_MAX_OUTPUT_TOKENS_PER_MONTH=75000",
        "",
    )).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary_path, 0, account.pw_gid)
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    validate_existing(output, account.pw_gid)
    return "installed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    try:
        result = install(bot_env=BOT_ENV, output=BLURB_ENV, owner=args.owner)
        print(f"blurb generation configuration: {result}; values hidden")
        return 0
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"blurb configuration rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
