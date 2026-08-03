#!/usr/bin/env python3
"""Run the fixed Pi curation job in shadow mode unless explicitly promoted."""
from __future__ import annotations

import os
from pathlib import Path

import curation


LIVE_MARKER = Path("/etc/bristolbusbot/social-live-enabled")

REQUIRED_ENV = (
    "BBB_SOCIAL_DB",
    "BBB_SOCIAL_APP_DB",
    "BBB_SOCIAL_AUDIT_DB",
    "BBB_SOCIAL_OUTPUT_DIR",
    "BBB_SOCIAL_CHANNEL_ID",
    "BBB_SOCIAL_ALLOWED_USER_ID",
)


def build_args(environment: dict[str, str], *, live_marker: Path = LIVE_MARKER,
               credential: Path | None = None) -> list[str]:
    missing = [name for name in REQUIRED_ENV if not environment.get(name)]
    if missing:
        raise RuntimeError(
            "social curation configuration is incomplete: " + ", ".join(missing))
    if credential is None:
        credential_root = environment.get("CREDENTIALS_DIRECTORY")
        if not credential_root:
            raise RuntimeError("systemd credential directory is unavailable")
        credential = Path(credential_root) / "slack-token"
    args = [
        "--db", environment["BBB_SOCIAL_DB"],
        "--app-db", environment["BBB_SOCIAL_APP_DB"],
        "--audit-db", environment["BBB_SOCIAL_AUDIT_DB"],
        "--output-dir", environment["BBB_SOCIAL_OUTPUT_DIR"],
        "--channel-id", environment["BBB_SOCIAL_CHANNEL_ID"],
        "--allowed-user-id", environment["BBB_SOCIAL_ALLOWED_USER_ID"],
        "--slack-credential", str(credential),
    ]
    if not live_marker.is_file():
        args.append("--shadow")
    return args


def main() -> int:
    return curation.main(build_args(dict(os.environ)))


if __name__ == "__main__":
    raise SystemExit(main())
