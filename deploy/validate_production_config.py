#!/usr/bin/env python3
"""Fail-closed production config checks without ever displaying a secret."""
from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


CONFIG_ROOT = Path("/etc/bristolbusbot")
EXPECTED = {
    "collector": {
        "BBB_LIVE_DB": "/var/lib/bristolbusbot/collector/live.db",
        "BBB_AUDIT_DB": "/var/lib/bristolbusbot/collector/audit.db",
        "BBB_TIMETABLE_DB": "/var/lib/bristolbusbot/pipeline/timetable.db",
        "BBB_TZ": "Europe/London",
        "BBB_ENABLE_EXACT_MATCH": "1",
    },
    "site": {
        "BBB_LIVE_DB": "/var/lib/bristolbusbot/collector/live.db",
        "BBB_TIMETABLE_DB": "/var/lib/bristolbusbot/pipeline/timetable.db",
        "BBB_ENFORCE_HTTPS": "true",
    },
    "bot": {
        "TEST_MODE": "false",
        "INGEST_MODE": "events",
        "PORT": "3010",
        "BSKY_HANDLE": "bristolbusbot.live",
        "LIVE_DB_PATH": "/var/lib/bristolbusbot/collector/live.db",
        "TIMETABLE_DB_PATH": "/var/lib/bristolbusbot/pipeline/timetable.db",
        "APP_DATA_DB_PATH": "/var/lib/bristolbusbot/bot/app_data.db",
        "ENABLE_FILE_LOGS": "false",
    },
}
SECRET_MINIMUMS = {
    "collector": {"BODS_API_KEY": 16},
    "bot": {"API_AUTH_TOKEN": 32, "BSKY_APP_PASSWORD": 8},
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def validate_private_file(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"refusing non-regular config file: {path}")
    if info.st_uid != 0:
        raise RuntimeError(f"config must be root-owned: {path}")
    if stat.S_IMODE(info.st_mode) & 0o027:
        raise RuntimeError(f"config is too broadly writable/readable: {path}")


def validate(component: str, root: Path = CONFIG_ROOT,
             path_override: Path | None = None) -> None:
    if component == "pipeline":
        component = "collector"
    if component == "tunnel":
        if path_override is not None:
            raise RuntimeError("--file is not supported for tunnel configuration")
        config = root / "cloudflared" / "config.yml"
        credential_files = list((root / "cloudflared").glob("*.json"))
        validate_private_file(config)
        if len(credential_files) != 1:
            raise RuntimeError("expected exactly one Cloudflare credential file")
        validate_private_file(credential_files[0])
        return
    if component not in EXPECTED:
        raise RuntimeError(f"unsupported component: {component}")
    path = path_override or root / f"{component}.env"
    validate_private_file(path)
    values = parse_env(path)
    errors = [f"{key} must equal the canonical production value"
              for key, expected in EXPECTED[component].items()
              if values.get(key) != expected]
    errors.extend(
        f"{key} is missing or implausibly short"
        for key, minimum in SECRET_MINIMUMS.get(component, {}).items()
        if len(values.get(key, "")) < minimum)
    if errors:
        raise RuntimeError("production configuration rejected (values hidden):\n- "
                           + "\n- ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=(*EXPECTED, "pipeline", "tunnel"))
    parser.add_argument("--root", type=Path, default=CONFIG_ROOT,
                        help=argparse.SUPPRESS)
    parser.add_argument("--file", type=Path, dest="path_override",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    validate(args.component, args.root, args.path_override)
    print(f"{args.component}: production configuration valid; values hidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
