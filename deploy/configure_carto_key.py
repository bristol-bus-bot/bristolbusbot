#!/usr/bin/env python3
"""Install a CARTO basemap key without printing or storing it locally.

Paste the complete CARTO tile URL from the key email at the hidden prompt. The
URL is validated locally, only its key is placed in a private candidate site
environment on the Pi, and the fixed root helper promotes it with rollback.
"""
from __future__ import annotations

import argparse
import getpass
import re
import stat
import sys
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlsplit

import paramiko

from local_config import DeploySettings, load_deploy_settings


KEY_RE = re.compile(r"[A-Za-z0-9._~-]{16,512}")
ENV_PATH = PurePosixPath("/etc/bristolbusbot/site.env")
CONTROL_COMMAND = "sudo -n /usr/local/sbin/bbb-deploy-control carto-key-promote"


def staging_path(settings: DeploySettings) -> PurePosixPath:
    return settings.remote_base / "incoming" / "site.env.carto-new"


def parse_key_from_url(value: str) -> str:
    """Return one plausible key from a genuine CARTO basemap HTTPS URL."""
    try:
        parts = urlsplit(value.strip())
    except ValueError as exc:
        raise ValueError("the CARTO URL is not valid") from exc
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port is not None
            or not (host == "basemaps.cartocdn.com"
                    or host.endswith(".basemaps.cartocdn.com"))):
        raise ValueError("paste the complete HTTPS CARTO basemap URL")
    values = parse_qs(parts.query, keep_blank_values=True).get("key", [])
    if len(values) != 1 or not KEY_RE.fullmatch(values[0]):
        raise ValueError("the CARTO URL does not contain one valid project key")
    return values[0]


def replace_key(env_text: str, key: str) -> str:
    """Replace duplicate key settings while preserving unrelated values."""
    result: list[str] = []
    replaced = False
    for line in env_text.splitlines():
        if line.startswith("BBB_CARTO_BASEMAP_KEY="):
            if not replaced:
                result.append(f"BBB_CARTO_BASEMAP_KEY={key}")
                replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"BBB_CARTO_BASEMAP_KEY={key}")
    return "\n".join(result) + "\n"


def connect(settings: DeploySettings) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(settings.host, username=settings.user, timeout=30)
    return ssh


def remove_staging(ssh: paramiko.SSHClient, path: PurePosixPath) -> None:
    """Remove only the fixed user-owned candidate path."""
    sftp = ssh.open_sftp()
    try:
        try:
            sftp.remove(str(path))
        except OSError:
            pass
    finally:
        sftp.close()


def promote_candidate(ssh: paramiko.SSHClient) -> None:
    """Invoke the one exact privileged action without echoing its input."""
    _, stdout, _ = ssh.exec_command(CONTROL_COMMAND)
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError("the Pi rejected the candidate; previous configuration retained")


def install_key(key: str) -> None:
    settings = load_deploy_settings()
    remote_staging = staging_path(settings)
    ssh = connect(settings)
    try:
        remove_staging(ssh, remote_staging)
        sftp = ssh.open_sftp()
        try:
            with sftp.open(str(ENV_PATH), "r") as handle:
                raw = handle.read()
            original = raw.decode() if isinstance(raw, bytes) else raw
            updated = replace_key(original, key)
            with sftp.open(str(remote_staging), "w") as handle:
                handle.write(updated)
            sftp.chmod(str(remote_staging), stat.S_IRUSR | stat.S_IWUSR)
        finally:
            sftp.close()
        promote_candidate(ssh)
    finally:
        try:
            remove_staging(ssh, remote_staging)
        finally:
            ssh.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read the complete CARTO URL from standard input",
    )
    args = parser.parse_args()
    supplied = sys.stdin.read() if args.stdin else getpass.getpass(
        "Paste the complete CARTO tile URL from the email: ")
    try:
        key = parse_key_from_url(supplied)
        install_key(key)
    except Exception:  # values and remote output must never reach the terminal
        print(
            "CARTO key installation failed; no key was printed and the previous "
            "site configuration was retained.",
            file=sys.stderr,
        )
        return 1
    print("CARTO key installed; the value was not stored on this computer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
