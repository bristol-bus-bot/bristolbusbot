#!/usr/bin/env python3
"""Interactively write the root-only Bristol Bus Bot backup configuration."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shlex
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


BUCKET = "bristolbusbot-backup"
HC_HOSTS = {"hc-ping.com"}


class ConfigurationError(ValueError):
    """An entered value is unsafe or incomplete."""


def validate_r2_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not parts.hostname.endswith(".r2.cloudflarestorage.com")
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ConfigurationError(
            "R2 endpoint must look like "
            "https://ACCOUNT_ID.r2.cloudflarestorage.com"
        )
    return urlunsplit(("https", parts.netloc, "", "", ""))


def validate_healthcheck_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    components = [item for item in parts.path.split("/") if item]
    if (
        parts.scheme != "https"
        or parts.hostname not in HC_HOSTS
        or parts.username is not None
        or parts.password is not None
        or len(components) != 1
        or not re.fullmatch(r"[A-Za-z0-9_-]+", components[0])
        or parts.query
        or parts.fragment
    ):
        raise ConfigurationError(
            "Healthchecks URL must be the base ping URL "
            "https://hc-ping.com/CHECK_UUID"
        )
    return urlunsplit(("https", parts.netloc, f"/{components[0]}", "", ""))


def validate_filesystem_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value.strip())
    except ValueError as exc:
        raise ConfigurationError("backup filesystem UUID is invalid") from exc
    if str(parsed) != value.strip().lower():
        raise ConfigurationError("backup filesystem UUID must be canonical")
    return str(parsed)


def render_remote_home(value: object, remote_home: str) -> object:
    if isinstance(value, dict):
        return {key: render_remote_home(item, remote_home)
                for key, item in value.items()}
    if isinstance(value, list):
        return [render_remote_home(item, remote_home) for item in value]
    if isinstance(value, str):
        return value.replace("@BBB_REMOTE_HOME@", remote_home)
    return value


def build_config(
    template: dict[str, object], endpoint: str, backup_uuid: str,
    remote_home: str,
) -> dict[str, object]:
    if not remote_home.startswith("/") or ".." in Path(remote_home).parts:
        raise ConfigurationError("remote home must be a safe absolute path")
    result = render_remote_home(template, remote_home)
    if not isinstance(result, dict):
        raise ConfigurationError("backup template must contain an object")
    result["expected_mount_source"] = f"/dev/disk/by-uuid/{backup_uuid}"
    result["r2_repository"] = f"s3:{endpoint}/{BUCKET}"
    return result


def render_env(
    access_key: str,
    secret_key: str,
    backup_healthcheck: str,
    check_healthcheck: str,
) -> str:
    values = {
        "BBB_BACKUP_CONFIG": "/etc/bristolbusbot/backup.json",
        "BBB_BACKUP_HEALTHCHECK_URL": backup_healthcheck,
        "BBB_BACKUP_CHECK_HEALTHCHECK_URL": check_healthcheck,
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_DEFAULT_REGION": "auto",
        "XDG_CACHE_HOME": "/var/cache/bristolbusbot",
    }
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def secret(prompt: str) -> str:
    value = getpass.getpass(prompt).strip()
    if not value:
        raise ConfigurationError(f"{prompt.rstrip(': ')} must not be empty")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("/tmp/backup.example.json"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/bristolbusbot/backup.json"),
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path("/etc/bristolbusbot/backup.env"),
    )
    parser.add_argument(
        "--filesystem-uuid",
        default=os.environ.get("BBB_BACKUP_UUID"),
        help="dedicated backup filesystem UUID (or set BBB_BACKUP_UUID)",
    )
    parser.add_argument(
        "--remote-home",
        default=os.environ.get("BBB_REMOTE_HOME"),
        help="deployment account home directory (or set BBB_REMOTE_HOME)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise SystemExit("configure_backup.py must run as root")
    if args.config.exists() or args.env.exists():
        raise SystemExit(
            "refusing to overwrite existing backup configuration; "
            "remove it explicitly after review if replacement is intended"
        )

    try:
        if not args.filesystem_uuid:
            raise ConfigurationError(
                "provide --filesystem-uuid or set BBB_BACKUP_UUID"
            )
        if not args.remote_home:
            raise ConfigurationError("provide --remote-home or set BBB_REMOTE_HOME")
        backup_uuid = validate_filesystem_uuid(args.filesystem_uuid)
        template = json.loads(args.template.read_text(encoding="utf-8"))
        endpoint = validate_r2_endpoint(
            input("Exact R2 S3 endpoint shown by Cloudflare: ")
        )
        access_key = secret("R2 Access Key ID (hidden): ")
        secret_key = secret("R2 Secret Access Key (hidden): ")
        backup_healthcheck = validate_healthcheck_url(
            secret("Nightly backup Healthchecks base ping URL (hidden): ")
        )
        check_healthcheck = validate_healthcheck_url(
            secret("Weekly repository-check Healthchecks base ping URL (hidden): ")
        )
    except (OSError, json.JSONDecodeError, ConfigurationError) as exc:
        raise SystemExit(f"configuration failed: {exc}") from exc

    config = build_config(template, endpoint, backup_uuid, args.remote_home)
    private_write(args.config, json.dumps(config, indent=2) + "\n")
    private_write(
        args.env,
        render_env(
            access_key,
            secret_key,
            backup_healthcheck,
            check_healthcheck,
        ),
    )
    print("Backup configuration written securely.")
    print(f"R2 repository: {endpoint}/{BUCKET}")
    print("Local backup filesystem identifier configured.")
    print("No secret values were displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
