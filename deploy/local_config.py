#!/usr/bin/env python3
"""Load workstation-only, non-secret production deployment identity."""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Mapping


HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "local.env"
KEYS = (
    "BBB_DEPLOY_USER",
    "BBB_DEPLOY_HOST",
    "BBB_REMOTE_HOME",
    "BBB_BACKUP_UUID",
    "BBB_CLOUDFLARE_TUNNEL_ID",
    "BBB_LOCAL_GTFS_DIR",
)
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
HOST_RE = re.compile(
    r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*"
)


class LocalConfigError(ValueError):
    """The local deployment configuration is absent or unsafe."""


@dataclass(frozen=True)
class DeploySettings:
    user: str
    host: str
    remote_home: PurePosixPath
    backup_uuid: str
    cloudflare_tunnel_id: str
    local_gtfs_dir: Path

    @property
    def remote_base(self) -> PurePosixPath:
        return self.remote_home / "bristolbusbot"

    @property
    def audit_repo_dir(self) -> PurePosixPath:
        return self.remote_home / "bus-audit-repo"

    @property
    def notify_script(self) -> PurePosixPath:
        return self.remote_home / "bin" / "notify_slack.sh"

    def with_overrides(
        self,
        *,
        user: str | None = None,
        host: str | None = None,
        remote_home: str | None = None,
    ) -> "DeploySettings":
        values = {
            "BBB_DEPLOY_USER": user or self.user,
            "BBB_DEPLOY_HOST": host or self.host,
            "BBB_REMOTE_HOME": remote_home or str(self.remote_home),
            "BBB_BACKUP_UUID": self.backup_uuid,
            "BBB_CLOUDFLARE_TUNNEL_ID": self.cloudflare_tunnel_id,
            "BBB_LOCAL_GTFS_DIR": str(self.local_gtfs_dir),
        }
        return settings_from(values)

    def render_tokens(self) -> dict[str, str]:
        return {
            "@BBB_DEPLOY_USER@": self.user,
            "@BBB_REMOTE_HOME@": str(self.remote_home),
            "@BBB_DEPLOY_BASE@": str(self.remote_base),
            "@BBB_CLOUDFLARE_TUNNEL_ID@": self.cloudflare_tunnel_id,
        }


def parse_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise LocalConfigError(
            f"missing {path}; copy deploy/local.env.example to deploy/local.env "
            "and replace the fictional values"
        ) from exc

    values: dict[str, str] = {}
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LocalConfigError(f"{path}:{lineno}: expected NAME=value")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in KEYS:
            raise LocalConfigError(f"{path}:{lineno}: unsupported setting {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not value or "\n" in value or "\r" in value:
            raise LocalConfigError(f"{path}:{lineno}: {key} is empty or unsafe")
        values[key] = value
    return values


def _canonical_uuid(value: str, key: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise LocalConfigError(f"{key} must be a canonical UUID") from exc
    if str(parsed) != value.lower():
        raise LocalConfigError(f"{key} must be a lowercase canonical UUID")
    return str(parsed)


def settings_from(values: Mapping[str, str]) -> DeploySettings:
    missing = [key for key in KEYS if not values.get(key)]
    if missing:
        raise LocalConfigError("missing local settings: " + ", ".join(missing))

    user = values["BBB_DEPLOY_USER"]
    host = values["BBB_DEPLOY_HOST"]
    home = PurePosixPath(values["BBB_REMOTE_HOME"])
    if not USER_RE.fullmatch(user):
        raise LocalConfigError("BBB_DEPLOY_USER is not a safe Linux account name")
    if not HOST_RE.fullmatch(host):
        raise LocalConfigError("BBB_DEPLOY_HOST is not a safe hostname")
    if (not home.is_absolute() or home == PurePosixPath("/")
            or ".." in home.parts
            or not re.fullmatch(r"/[A-Za-z0-9._/-]+", str(home))):
        raise LocalConfigError("BBB_REMOTE_HOME must be a safe absolute path")

    return DeploySettings(
        user=user,
        host=host,
        remote_home=home,
        backup_uuid=_canonical_uuid(values["BBB_BACKUP_UUID"], "BBB_BACKUP_UUID"),
        cloudflare_tunnel_id=_canonical_uuid(
            values["BBB_CLOUDFLARE_TUNNEL_ID"], "BBB_CLOUDFLARE_TUNNEL_ID"
        ),
        local_gtfs_dir=Path(values["BBB_LOCAL_GTFS_DIR"]),
    )


def load_deploy_settings(path: Path = DEFAULT_PATH) -> DeploySettings:
    values = parse_env(path)
    for key in KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    return settings_from(values)
