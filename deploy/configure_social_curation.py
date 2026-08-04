#!/usr/bin/env python3
"""Write the root-owned Slack curation configuration without exposing its token."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import stat
from pathlib import Path


CONFIG_ROOT = Path("/etc/bristolbusbot")
CHANNEL_RE = re.compile(r"C[A-Z0-9]{8,20}")
USER_RE = re.compile(r"U[A-Z0-9]{8,20}")

FIXED_VALUES = {
    "BBB_SOCIAL_DB": "/var/lib/bristolbusbot/social/social.db",
    "BBB_SOCIAL_APP_DB": "/var/lib/bristolbusbot/bot/app_data.db",
    "BBB_SOCIAL_AUDIT_DB": "/var/lib/bristolbusbot/collector/audit.db",
    "BBB_SOCIAL_OUTPUT_DIR": "/var/lib/bristolbusbot/social/cards",
}


def _identifier(value: str, pattern: re.Pattern[str], label: str) -> str:
    cleaned = value.strip()
    if not pattern.fullmatch(cleaned):
        raise ValueError(f"{label} is not a canonical Slack ID")
    return cleaned


def _token(value: str) -> str:
    cleaned = value.strip()
    if (not cleaned.startswith("xoxb-") or len(cleaned) < 20
            or len(cleaned) > 512 or any(char.isspace() for char in cleaned)):
        raise ValueError("Slack token is not a plausible bot token")
    return cleaned


def _safe_existing(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"refusing unsafe existing path: {path}")


def _write_candidate(path: Path, text: str, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def configure(root: Path, channel_id: str, user_id: str, token: str, *,
              replace: bool = False) -> tuple[Path, Path]:
    root = root.absolute()
    if not root.is_absolute() or root == Path(root.anchor) or root.is_symlink():
        raise RuntimeError("configuration root is unsafe")
    if not root.is_dir():
        raise RuntimeError(f"configuration root does not exist: {root}")
    channel = _identifier(channel_id, CHANNEL_RE, "channel ID")
    user = _identifier(user_id, USER_RE, "user ID")
    bot_token = _token(token)

    environment_path = root / "social.env"
    token_path = root / "social-slack.token"
    targets = (environment_path, token_path)
    for target in targets:
        if target.exists() or target.is_symlink():
            _safe_existing(target)
            if not replace:
                raise RuntimeError(
                    f"{target} already exists; use --replace deliberately")

    environment_candidate = root / ".social.env.new"
    token_candidate = root / ".social-slack.token.new"
    for candidate in (environment_candidate, token_candidate):
        if candidate.exists() or candidate.is_symlink():
            raise RuntimeError(f"stale configuration candidate exists: {candidate}")

    values = {
        **FIXED_VALUES,
        "BBB_SOCIAL_CHANNEL_ID": channel,
        "BBB_SOCIAL_ALLOWED_USER_ID": user,
    }
    environment = "".join(f"{key}={value}\n" for key, value in values.items())
    try:
        _write_candidate(environment_candidate, environment, 0o640)
        _write_candidate(token_candidate, bot_token + "\n", 0o600)
        os.replace(environment_candidate, environment_path)
        os.replace(token_candidate, token_path)
    finally:
        environment_candidate.unlink(missing_ok=True)
        token_candidate.unlink(missing_ok=True)
    return environment_path, token_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id")
    parser.add_argument("--allowed-user-id")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--root", type=Path, default=CONFIG_ROOT,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        parser.error("run this configuration helper through sudo")
    channel = args.channel_id or input("Private Slack channel ID: ")
    user = args.allowed_user_id or input("Allowlisted Slack user ID: ")
    token = getpass.getpass("Slack bot token (input hidden): ")
    configure(args.root, channel, user, token, replace=args.replace)
    print("Slack curation configuration installed; values hidden.")
    print("The timer remains disabled and live delivery remains off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
