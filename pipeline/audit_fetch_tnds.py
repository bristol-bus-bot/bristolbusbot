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


def main():
    if not USER or not PASS:
        print("ERROR: TNDS_USER / TNDS_PASS not set in .env.")
        print("Register free at https://www.travelinedata.org.uk/ then add them to .env.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{REGION}.zip"

    print(f"Connecting to {FTP_HOST} ...")
    try:
        ftp = FTP(host=FTP_HOST, user=USER, passwd=PASS, timeout=180)
    except Exception as e:
        print(f"ERROR: FTP login failed ({e}). Check TNDS_USER / TNDS_PASS.")
        return 1
    ftp.cwd(FTP_DIR)

    try:
        size = ftp.size(f"{REGION}.zip")
    except Exception:
        size = None
    print(f"Downloading {REGION}.zip" + (f" ({size // (1024*1024)} MB)" if size else "") + " ...")
    with open(out, "wb") as f:
        ftp.retrbinary(f"RETR {REGION}.zip", f.write)
    ftp.quit()

    mb = out.stat().st_size / (1024 * 1024)
    print(f"Saved {out} ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
