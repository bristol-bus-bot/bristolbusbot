#!/usr/bin/env python3
"""Download and verify one private collector research archive from the Pi."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import paramiko

from collector_research_export import (
    ARCHIVE_NAME_RE,
    DATABASE_MEMBER,
    MAX_ARCHIVE_BYTES,
    MAX_DATABASE_BYTES,
    README_MEMBER,
    SCHEMA_VERSION,
)
from local_config import DeploySettings, LocalConfigError, load_deploy_settings


REPO = Path(__file__).resolve().parent.parent
REMOTE_EXPORT_ROOT = PurePosixPath("/var/lib/bristolbusbot/private-exports")
REMOTE_COMMAND = "/usr/local/bin/bbb-collector-research-export"
MAX_README_BYTES = 1024 * 1024
MAX_ZIP_COMMENT_BYTES = 65535
PUBLIC_DIRECTORY_NAMES = {
    ".git", "audit-site", "bus-audit-repo", "html", "public",
    "public_html", "weca-bus-audit", "www",
}


class ResearchDownloadError(RuntimeError):
    """An expected, safe reason the private download cannot complete."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(settings: DeploySettings) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        settings.host, username=settings.user, timeout=30,
        banner_timeout=30, auth_timeout=30,
    )
    return client


def validate_remote_filename(value: str) -> str:
    name = PurePosixPath(value).name
    if name != value or not ARCHIVE_NAME_RE.fullmatch(name):
        raise ResearchDownloadError("remote recovery filename is unsafe")
    return name


def run_remote_export(ssh: paramiko.SSHClient, *, request_id: str,
                      from_value: str | None, to_value: str | None) -> dict:
    arguments = [REMOTE_COMMAND, "--request-id", request_id]
    if from_value:
        arguments.extend(("--from", from_value))
    if to_value:
        arguments.extend(("--to", to_value))
    command = " ".join(shlex.quote(value) for value in arguments)
    _, stdout, stderr = ssh.exec_command(command, timeout=15 * 60)
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    status = stdout.channel.recv_exit_status()
    if status:
        reason = error or output or f"remote exporter exited {status}"
        raise ResearchDownloadError(f"Pi research export failed: {reason}")
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise ResearchDownloadError("Pi exporter returned an invalid status record") from exc
    required = {
        "status", "schema_version", "remote_filename", "date_from", "date_to",
        "archive_bytes", "archive_sha256", "database_bytes", "database_sha256",
        "row_counts", "source_read_only", "source_connection_total_changes",
    }
    missing = required - set(payload)
    if missing:
        raise ResearchDownloadError(
            "Pi exporter status is missing: " + ", ".join(sorted(missing)))
    if payload["status"] != "created" or payload["schema_version"] != SCHEMA_VERSION:
        raise ResearchDownloadError("Pi exporter returned an unsupported result")
    validate_remote_filename(str(payload["remote_filename"]))
    if payload["source_read_only"] is not True \
            or payload["source_connection_total_changes"] != 0:
        raise ResearchDownloadError("Pi exporter did not prove read-only source access")
    return payload


def is_regular_zip_info(item: zipfile.ZipInfo) -> bool:
    mode = item.external_attr >> 16
    return not item.is_dir() and (mode == 0 or stat.S_ISREG(mode))


def archive_comment(archive: zipfile.ZipFile) -> dict:
    if not archive.comment or len(archive.comment) > MAX_ZIP_COMMENT_BYTES:
        raise ResearchDownloadError("archive manifest comment is missing or oversized")
    try:
        result = json.loads(archive.comment.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ResearchDownloadError("archive manifest comment is invalid") from exc
    required = {
        "schema_version", "date_from", "date_to", "generated_at", "selection",
        "database_member", "database_bytes", "database_sha256",
        "readme_member", "readme_sha256", "row_counts",
    }
    missing = required - set(result)
    if missing:
        raise ResearchDownloadError(
            "archive manifest is missing: " + ", ".join(sorted(missing)))
    if result["schema_version"] != SCHEMA_VERSION \
            or result["selection"] != "complete_census_no_sampling":
        raise ResearchDownloadError("archive schema or selection is unsupported")
    if result["database_member"] != DATABASE_MEMBER \
            or result["readme_member"] != README_MEMBER:
        raise ResearchDownloadError("archive manifest names unexpected members")
    if not isinstance(result["row_counts"], dict) or not result["row_counts"]:
        raise ResearchDownloadError("archive manifest has no table row counts")
    return result


def validate_archive(path: Path, *, expected: dict | None = None) -> dict:
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ResearchDownloadError("downloaded archive size is outside the safety limit")
    archive_sha = sha256_file(path)
    if expected is not None:
        if path.stat().st_size != int(expected["archive_bytes"]):
            raise ResearchDownloadError("downloaded archive byte count differs from the Pi")
        if archive_sha != expected["archive_sha256"]:
            raise ResearchDownloadError("downloaded archive SHA-256 differs from the Pi")

    validation_dir = Path(tempfile.mkdtemp(
        prefix=f".{path.name}.validate-", dir=path.parent))
    os.chmod(validation_dir, 0o700)
    database_path = validation_dir / DATABASE_MEMBER
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ResearchDownloadError("downloaded ZIP failed CRC verification")
            members = archive.infolist()
            if {item.filename for item in members} != {DATABASE_MEMBER, README_MEMBER}:
                raise ResearchDownloadError("downloaded ZIP contains unexpected files")
            if any(not is_regular_zip_info(item) or item.flag_bits & 0x1 for item in members):
                raise ResearchDownloadError("downloaded ZIP contains unsafe entries")
            manifest = archive_comment(archive)
            database_info = archive.getinfo(DATABASE_MEMBER)
            readme_info = archive.getinfo(README_MEMBER)
            if database_info.file_size <= 0 or database_info.file_size > MAX_DATABASE_BYTES:
                raise ResearchDownloadError("database member is outside the size limit")
            if readme_info.file_size <= 0 or readme_info.file_size > MAX_README_BYTES:
                raise ResearchDownloadError("README member is outside the size limit")
            if database_info.file_size != int(manifest["database_bytes"]):
                raise ResearchDownloadError("database size differs from the archive manifest")
            with archive.open(DATABASE_MEMBER) as source, database_path.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(database_path, 0o600)
            readme = archive.read(README_MEMBER)
            try:
                readme.decode("utf-8")
            except UnicodeError as exc:
                raise ResearchDownloadError("README is not valid UTF-8") from exc
            if hashlib.sha256(readme).hexdigest() != manifest["readme_sha256"]:
                raise ResearchDownloadError("README SHA-256 differs from the archive manifest")

        if sha256_file(database_path) != manifest["database_sha256"]:
            raise ResearchDownloadError("database SHA-256 differs from the archive manifest")
        if expected is not None and manifest["database_sha256"] != expected["database_sha256"]:
            raise ResearchDownloadError("database SHA-256 differs from the Pi status")
        connection = sqlite3.connect(
            database_path.as_uri() + "?mode=ro", uri=True, timeout=60)
        try:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ResearchDownloadError("downloaded database failed SQLite quick_check")
            objects = connection.execute(
                "SELECT type,name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            if any(kind != "table" for kind, _ in objects):
                raise ResearchDownloadError("downloaded database contains executable schema objects")
            db_manifest = dict(connection.execute(
                "SELECT key,value FROM research_manifest"))
            if db_manifest.get("schema_version") != str(SCHEMA_VERSION) \
                    or db_manifest.get("selection") != "complete_census_no_sampling" \
                    or db_manifest.get("source_read_only") != "true" \
                    or db_manifest.get("source_connection_total_changes") != "0":
                raise ResearchDownloadError("database manifest failed the safety assertions")
            if db_manifest.get("date_from") != manifest["date_from"] \
                    or db_manifest.get("date_to") != manifest["date_to"]:
                raise ResearchDownloadError("database date range differs from the archive manifest")
            table_manifest = {
                row[0]: int(row[1]) for row in connection.execute(
                    "SELECT table_name,exported_rows FROM research_table_manifest")
            }
            for name, expected_rows in table_manifest.items():
                quoted = '"' + name.replace('"', '""') + '"'
                actual = connection.execute(
                    f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                if actual != expected_rows:
                    raise ResearchDownloadError(f"row-count verification failed for {name}")
            for name, expected_rows in manifest["row_counts"].items():
                if table_manifest.get(name) != int(expected_rows):
                    raise ResearchDownloadError(
                        f"archive and database row counts differ for {name}")
        except sqlite3.Error as exc:
            raise ResearchDownloadError(f"downloaded database is invalid: {exc}") from exc
        finally:
            connection.close()
        return {**manifest, "archive_sha256": archive_sha,
                "archive_bytes": path.stat().st_size}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ResearchDownloadError(f"downloaded ZIP is invalid: {exc}") from exc
    finally:
        shutil.rmtree(validation_dir, ignore_errors=True)


def reject_public_or_repo_path(path: Path) -> None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(REPO.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ResearchDownloadError("refusing to put private research data inside the source repository")
    lowered = {part.lower() for part in resolved.parts}
    matched = sorted(lowered & PUBLIC_DIRECTORY_NAMES)
    if matched:
        raise ResearchDownloadError(
            f"refusing known public/repository directory '{matched[0]}'")


def output_parent(value: Path) -> tuple[Path, Path | None]:
    expanded = value.expanduser()
    explicit: Path | None = None
    if expanded.exists():
        if expanded.is_symlink():
            raise ResearchDownloadError("refusing symbolic-link output path")
        if expanded.is_dir():
            parent = expanded.resolve(strict=True)
        else:
            raise ResearchDownloadError("output already exists; choose a new file or directory")
    elif expanded.suffix.lower() == ".zip":
        parent = expanded.parent.resolve(strict=True)
        explicit = parent / expanded.name
    else:
        raise ResearchDownloadError(
            "--output must be an existing private directory or a new .zip filename")
    reject_public_or_repo_path(parent)
    if explicit:
        reject_public_or_repo_path(explicit)
        if explicit.exists() or explicit.is_symlink():
            raise ResearchDownloadError("output file already exists")
    return parent, explicit


def remote_path(filename: str) -> PurePosixPath:
    return REMOTE_EXPORT_ROOT / validate_remote_filename(filename)


def remote_regular_private(sftp: paramiko.SFTPClient,
                           path: PurePosixPath) -> object:
    try:
        info = sftp.lstat(str(path))
    except FileNotFoundError as exc:
        raise ResearchDownloadError("remote recovery archive does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ResearchDownloadError("remote archive is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ResearchDownloadError("remote archive permissions are not private")
    if info.st_size <= 0 or info.st_size > MAX_ARCHIVE_BYTES:
        raise ResearchDownloadError("remote archive size is outside the safety limit")
    return info


def download_archive(sftp: paramiko.SFTPClient, filename: str,
                     parent: Path) -> Path:
    remote = remote_path(filename)
    info = remote_regular_private(sftp, remote)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{filename}.", suffix=".part", dir=parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    os.chmod(temporary_path, 0o600)
    try:
        sftp.get(str(remote), str(temporary_path))
        if temporary_path.stat().st_size != info.st_size:
            raise ResearchDownloadError("downloaded byte count differs from the remote file")
        return temporary_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def remove_remote(sftp: paramiko.SFTPClient, filename: str) -> None:
    path = remote_path(filename)
    remote_regular_private(sftp, path)
    sftp.remove(str(path))
    try:
        sftp.lstat(str(path))
    except FileNotFoundError:
        return
    raise ResearchDownloadError("remote archive still exists after cleanup")


def final_filename(manifest: dict) -> str:
    return f"collector-research-{manifest['date_from']}-to-{manifest['date_to']}.zip"


def publish_local(temporary: Path, final: Path) -> None:
    if final.exists() or final.is_symlink():
        raise ResearchDownloadError("output appeared during download; no file was replaced")
    try:
        os.link(temporary, final)
    except FileExistsError as exc:
        raise ResearchDownloadError(
            "output appeared during download; no file was replaced") from exc
    os.chmod(final, 0o600)
    temporary.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_value",
                        help="first service date; default is earliest retained")
    parser.add_argument("--to", dest="to_value",
                        help="last closed service date; default is latest retained closed day")
    parser.add_argument(
        "--output", type=Path, default=Path.home() / "Downloads",
        help="existing private directory or new .zip filename (default: Downloads)")
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--resume", metavar="REMOTE_FILENAME",
                          help="download a safely retained Pi archive after an interruption")
    recovery.add_argument("--cleanup", metavar="REMOTE_FILENAME",
                          help="remove one exact safely named retained Pi archive")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ssh: paramiko.SSHClient | None = None
    temporary: Path | None = None
    created_remote: str | None = None
    local_published = False
    try:
        settings = load_deploy_settings()
        parent, explicit = output_parent(args.output)
        ssh = connect(settings)
        sftp = ssh.open_sftp()
        try:
            if args.cleanup:
                remove_remote(sftp, validate_remote_filename(args.cleanup))
                print("Removed the exact retained private research archive from the Pi.")
                return 0
            expected = None
            if args.resume:
                filename = validate_remote_filename(args.resume)
            else:
                request_id = uuid.uuid4().hex[:12]
                expected = run_remote_export(
                    ssh, request_id=request_id,
                    from_value=args.from_value, to_value=args.to_value)
                filename = str(expected["remote_filename"])
                created_remote = filename
            temporary = download_archive(sftp, filename, parent)
            manifest = validate_archive(temporary, expected=expected)
            final = explicit or (parent / final_filename(manifest))
            reject_public_or_repo_path(final)
            publish_local(temporary, final)
            temporary = None
            local_published = True
            remove_remote(sftp, filename)
        finally:
            sftp.close()
        print(
            f"Downloaded private collector research dataset: {final} "
            f"({manifest['archive_bytes']} bytes; "
            f"{sum(int(value) for value in manifest['row_counts'].values())} source rows)")
        return 0
    except (ResearchDownloadError, LocalConfigError, OSError,
            paramiko.SSHException, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if created_remote and not local_published:
            print(
                "The private Pi archive was left in place for recovery. Re-run: "
                f"python deploy/get_collector_research_export.py --resume {created_remote} "
                f"--output {shlex.quote(str(args.output))}",
                file=sys.stderr,
            )
        elif created_remote and local_published:
            print(
                "The verified local file is safe, but Pi cleanup needs retrying: "
                f"python deploy/get_collector_research_export.py --cleanup {created_remote}",
                file=sys.stderr,
            )
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if ssh is not None:
            ssh.close()


if __name__ == "__main__":
    raise SystemExit(main())
