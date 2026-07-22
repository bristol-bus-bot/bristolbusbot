#!/usr/bin/env python3
"""
Download a Traveline National Dataset (TNDS) regional TransXChange file.

TNDS is the older national bus timetable feed. It contains First Bristol
services that are NOT published to BODS (e.g. routes 42-45), which is where
bustimes.org gets them. The South West region file (SW.zip) covers Bristol.

Needs free TNDS FTP credentials from https://www.travelinedata.org.uk/
(register, then add to .env):
    TNDS_USER=your_username
    TNDS_PASS=your_password

Usage:
    python audit_fetch_tnds.py            # downloads SW (South West)
    python audit_fetch_tnds.py SW
"""
import os
import sys
import tempfile
import time
import zipfile
from ftplib import FTP, error_perm
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
USER = os.getenv("TNDS_USER")
PASS = os.getenv("TNDS_PASS")
REGION = (sys.argv[1] if len(sys.argv) > 1 else "SW").upper()
OUT_DIR = Path(tempfile.gettempdir()) / "busaudit_tnds"
FTP_HOST = "ftp.tnds.basemap.co.uk"
FTP_DIR = "TNDSV2.5"
MAX_ATTEMPTS = 3
STALL_TIMEOUT_SECONDS = 180
PROGRESS_BYTES = 16 * 1024 * 1024


def safe_error(exc):
    """Remove FTP credentials from any exception written to public logs."""
    message = str(exc)
    for secret in (USER, PASS):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message}"


def validate_archive(path, expected_size):
    actual = path.stat().st_size if path.exists() else 0
    if actual == 0:
        raise ValueError("downloaded archive is empty")
    if expected_size is not None and actual != expected_size:
        raise ValueError(
            f"download size mismatch: expected {expected_size}, got {actual}")
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise zipfile.BadZipFile(f"CRC failure in {bad_member}")
    return actual


def download_archive(destination, *, ftp_factory=FTP, sleep=time.sleep,
                     max_attempts=MAX_ATTEMPTS):
    """Download, resume after stalls, and publish only a verified TNDS ZIP."""
    part = destination.with_suffix(destination.suffix + ".part")
    last_error = None
    for attempt in range(1, max_attempts + 1):
        ftp = None
        remote_size = None
        try:
            print(
                f"TNDS attempt {attempt}/{max_attempts}: connecting to "
                f"{FTP_HOST} ...", flush=True)
            ftp = ftp_factory(
                host=FTP_HOST, user=USER, passwd=PASS,
                timeout=STALL_TIMEOUT_SECONDS)
            ftp.cwd(FTP_DIR)
            try:
                remote_size = ftp.size(destination.name)
            except Exception:
                remote_size = None

            offset = part.stat().st_size if part.exists() else 0
            if remote_size is None and offset:
                print(
                    "TNDS server did not provide a size; restarting safely.",
                    flush=True)
                part.unlink(missing_ok=True)
                offset = 0
            elif remote_size is not None and offset > remote_size:
                print(
                    "Partial TNDS file is larger than the source; restarting.",
                    flush=True)
                part.unlink(missing_ok=True)
                offset = 0

            def retrieve(start):
                downloaded = start
                next_report = ((start // PROGRESS_BYTES) + 1) * PROGRESS_BYTES
                mode = "ab" if start else "wb"
                action = "Resuming" if start else "Downloading"
                total_text = (
                    f" of {remote_size // (1024 * 1024)} MB"
                    if remote_size is not None else "")
                print(
                    f"{action} {destination.name} at "
                    f"{start // (1024 * 1024)} MB{total_text} ...",
                    flush=True)

                with open(part, mode) as output:
                    def write_chunk(chunk):
                        nonlocal downloaded, next_report
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            print(
                                f"  TNDS progress: "
                                f"{downloaded // (1024 * 1024)} MB",
                                flush=True)
                            next_report += PROGRESS_BYTES

                    ftp.retrbinary(
                        f"RETR {destination.name}", write_chunk,
                        blocksize=1024 * 1024,
                        rest=start if start else None)

            if remote_size is None or offset != remote_size:
                try:
                    retrieve(offset)
                except error_perm:
                    if not offset:
                        raise
                    print(
                        "TNDS server refused resume; restarting this attempt.",
                        flush=True)
                    part.unlink(missing_ok=True)
                    retrieve(0)

            try:
                ftp.quit()
            except Exception:
                ftp.close()
            ftp = None
            actual = validate_archive(part, remote_size)
            os.replace(part, destination)
            return actual
        except Exception as exc:
            last_error = exc
            if ftp is not None:
                try:
                    ftp.close()
                except Exception:
                    pass
            if isinstance(exc, zipfile.BadZipFile):
                part.unlink(missing_ok=True)
            partial = part.stat().st_size if part.exists() else 0
            print(
                f"TNDS attempt {attempt} stopped after "
                f"{partial // (1024 * 1024)} MB: {safe_error(exc)}",
                flush=True)
            if attempt < max_attempts:
                sleep(2 ** (attempt - 1))

    part.unlink(missing_ok=True)
    raise RuntimeError(
        f"TNDS download failed after {max_attempts} attempts: "
        f"{safe_error(last_error)}")


def main():
    if not USER or not PASS:
        print("ERROR: TNDS_USER / TNDS_PASS not set in .env.")
        print("Register free at https://www.travelinedata.org.uk/ then add them to .env.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{REGION}.zip"

    try:
        download_archive(out)
    except Exception as e:
        print(
            f"ERROR: TNDS download failed ({safe_error(e)}). "
            "Check credentials/network.")
        return 1

    mb = out.stat().st_size / (1024 * 1024)
    print(f"Saved {out} ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
