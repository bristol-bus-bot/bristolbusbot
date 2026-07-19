#!/usr/bin/env python3
"""Rotate the systemd bot control token without exposing it in terminal output.

The new value is written to a caller-selected private local file and uploaded
to a fixed staging path on the Pi. A tightly allowlisted root helper validates
and promotes it, restarts ``bbb-bot.service``, and restores the old environment
automatically if its health check fails. No secret is passed in a command,
logged, or printed.

Example (the output path must be outside this repository)::

    python deploy/rotate_bot_token.py --output "$HOME/.bbb-bot-api-token"
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import stat
import subprocess
from pathlib import Path, PurePosixPath

import paramiko

from local_config import DeploySettings, load_deploy_settings

REPO = Path(__file__).resolve().parent.parent
ENV_PATH = PurePosixPath("/etc/bristolbusbot/bot.env")
CONTROL_COMMAND = (
    "sudo -n /usr/local/sbin/bbb-deploy-control bot-token-promote")


def staging_path(settings: DeploySettings) -> PurePosixPath:
    return settings.remote_base / "incoming" / "bot.env.token-new"


def connect(settings: DeploySettings) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(settings.host, username=settings.user, timeout=30)
    return ssh


def replace_token(env_text: str, token: str) -> str:
    lines = env_text.splitlines()
    result = []
    replaced = False
    for line in lines:
        if line.startswith("API_AUTH_TOKEN="):
            if not replaced:
                result.append(f"API_AUTH_TOKEN={token}")
                replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"API_AUTH_TOKEN={token}")
    return "\n".join(result) + "\n"


def protect_local_file(path: Path) -> None:
    """Restrict the token file for the current user on POSIX and Windows."""
    if os.name == "nt":
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r",
             f"{getpass.getuser()}:(R,W)"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("could not restrict token file ACL")
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def remove_staging(ssh: paramiko.SSHClient, path: PurePosixPath) -> None:
    """Remove a user-owned candidate left before root promotion."""
    sftp = ssh.open_sftp()
    try:
        try:
            sftp.remove(str(path))
        except OSError:
            pass
    finally:
        sftp.close()


def promote_candidate(ssh: paramiko.SSHClient) -> None:
    """Invoke the one exact privileged action; never echo its input."""
    _, stdout, _ = ssh.exec_command(CONTROL_COMMAND)
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError(
            "the Pi rejected the candidate or failed its systemd health gate; "
            "the previous bot environment was restored")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True,
                        help="private local file outside the repository")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    try:
        output.relative_to(REPO)
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the repository")
    if output.exists():
        parser.error(f"refusing to overwrite existing file: {output}")

    token = secrets.token_urlsafe(48)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(token + "\n", encoding="utf-8")
    ssh = None
    try:
        settings = load_deploy_settings()
        remote_staging = staging_path(settings)
        protect_local_file(output)
        ssh = connect(settings)
        try:
            remove_staging(ssh, remote_staging)
            sftp = ssh.open_sftp()
            try:
                with sftp.open(str(ENV_PATH), "r") as handle:
                    raw = handle.read()
                original = raw.decode() if isinstance(raw, bytes) else raw
                updated = replace_token(original, token)
                with sftp.open(str(remote_staging), "w") as handle:
                    handle.write(updated)
                sftp.chmod(
                    str(remote_staging), stat.S_IRUSR | stat.S_IWUSR)
            finally:
                sftp.close()
            promote_candidate(ssh)
            print(f"Token rotated. The new value is stored only in: {output}")
            return 0
        finally:
            try:
                remove_staging(ssh, remote_staging)
            finally:
                ssh.close()
    except Exception as exc:
        try:
            output.unlink()
        except OSError:
            pass
        print(f"Token rotation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
