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
import zipfile
from ftplib import FTP
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
USER = os.getenv("TNDS_USER")
PASS = os.getenv("TNDS_PASS")
REGION = (sys.argv[1] if len(sys.argv) > 1 else "SW").upper()
OUT_DIR = Path(tempfile.gettempdir()) / "busaudit_tnds"
FTP_HOST = "ftp.tnds.basemap.co.uk"
FTP_DIR = "TNDSV2.5"


def safe_error(exc):
    """Remove FTP credentials from any exception written to public logs."""
    message = str(exc)
    for secret in (USER, PASS):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message}"


def main():
    if not USER or not PASS:
        print("ERROR: TNDS_USER / TNDS_PASS not set in .env.")
        print("Register free at https://www.travelinedata.org.uk/ then add them to .env.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{REGION}.zip"

    print(f"Connecting to {FTP_HOST} ...")
    ftp = None
    part = out.with_suffix(out.suffix + ".part")
    part.unlink(missing_ok=True)
    try:
        ftp = FTP(host=FTP_HOST, user=USER, passwd=PASS, timeout=180)
        ftp.cwd(FTP_DIR)
        try:
            size = ftp.size(f"{REGION}.zip")
        except Exception:
            size = None
        print(f"Downloading {REGION}.zip" +
              (f" ({size // (1024*1024)} MB)" if size else "") + " ...")
        with open(part, "wb") as output:
            ftp.retrbinary(f"RETR {REGION}.zip", output.write)
        ftp.quit()
        ftp = None
        actual = part.stat().st_size
        if actual == 0:
            raise ValueError("downloaded archive is empty")
        if size is not None and actual != size:
            raise ValueError(
                f"download size mismatch: expected {size}, got {actual}")
        with zipfile.ZipFile(part) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise zipfile.BadZipFile(f"CRC failure in {bad_member}")
        os.replace(part, out)
    except Exception as e:
        part.unlink(missing_ok=True)
        if ftp is not None:
            try:
                ftp.close()
            except Exception:
                pass
        print(
            f"ERROR: TNDS download failed ({safe_error(e)}). "
            "Check credentials/network.")
        return 1

    mb = out.stat().st_size / (1024 * 1024)
    print(f"Saved {out} ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
