#!/usr/bin/env python3
"""
Fetch First Bristol's own TransXChange datasets directly from the BODS API.

The BODS "GTFS" download is a lossy cached conversion of TransXChange that drops
complex routes (e.g. First's 42-45). The registration data underneath is
complete. This pulls First Bristol's actual TransXChange datasets (NOC FBRI)
from the BODS Timetables API so we can convert them ourselves and stop depending
on the lossy regional GTFS.

Writes the downloaded TXC zips to a temp folder and prints what it found
(dataset id, name, how many of routes 42-45 each contains).

    python audit_fetch_first_txc.py
Needs BODS_API_KEY in .env (same key the collector uses).
"""
import os
import re
import shutil
import sys
import time
import zipfile
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BODS_API_KEY")
NOC = "FBRI"
API = "https://data.bus-data.dft.gov.uk/api/v1/dataset/"
OUT_DIR = Path(tempfile.gettempdir()) / "busaudit_first_txc"
WATCH = {"42", "43", "44", "45"}
USER_AGENT = "BristolBusBot timetable builder (+https://bristolbuses.live/)"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def safe_error(exc):
    """Keep credential-bearing query strings out of public build logs."""
    message = str(exc)
    message = re.sub(
        r"([?&]api_key=)[^&\s'\"]+", r"\1[REDACTED]", message,
        flags=re.IGNORECASE)
    if API_KEY:
        message = message.replace(API_KEY, "[REDACTED]")
    return f"{type(exc).__name__}: {message}"


def discard_directory(path):
    if path.exists():
        shutil.rmtree(path)


def publish_directory(staging, destination):
    """Replace a source cache only after the complete new set is valid."""
    previous = destination.with_name(f".{destination.name}.old-{os.getpid()}")
    discard_directory(previous)
    if destination.exists():
        os.replace(destination, previous)
    try:
        os.replace(staging, destination)
    except OSError:
        if previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    discard_directory(previous)


def list_datasets(session):
    datasets = []
    url = f"{API}?api_key={API_KEY}&noc={NOC}&limit=100"
    while url:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
        datasets.extend(data.get("results", []))
        url = data.get("next")
    return datasets


def scan_txc_for_lines(path):
    """Return the set of watched route numbers (42-45) present as LineName in a
    TransXChange zip, without a full parse."""
    found = set()
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                raw = zf.read(name)
                for ln in WATCH:
                    if f"<LineName>{ln}</LineName>".encode() in raw:
                        found.add(ln)
    except zipfile.BadZipFile:
        pass
    return found


def download_archive(session, url, destination):
    """Stream and validate one required dataset with bounded retries."""
    part = destination.with_suffix(destination.suffix + ".part")
    part.unlink(missing_ok=True)
    last_error = None
    for attempt in range(1, 4):
        total = 0
        try:
            with session.get(url, stream=True, timeout=(30, 180)) as response:
                response.raise_for_status()
                expected = response.headers.get("Content-Length")
                expected_size = int(expected) if expected and expected.isdigit() else None
                with open(part, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_ARCHIVE_BYTES:
                            raise ValueError("archive exceeds 512 MiB safety limit")
                        output.write(chunk)
            if total == 0:
                raise ValueError("empty response body")
            if expected_size is not None and total != expected_size:
                raise ValueError(
                    f"content length mismatch: expected {expected_size}, got {total}")
            with zipfile.ZipFile(part) as archive:
                bad_member = archive.testzip()
                if bad_member:
                    raise zipfile.BadZipFile(f"CRC failure in {bad_member}")
            os.replace(part, destination)
            return total
        except (OSError, ValueError, zipfile.BadZipFile,
                requests.RequestException) as exc:
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(
        f"download failed after 3 attempts: {safe_error(last_error)}")


def main():
    if not API_KEY:
        print("ERROR: BODS_API_KEY not found in .env")
        return 1
    OUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT_DIR.with_name(f".{OUT_DIR.name}.new-{os.getpid()}")
    discard_directory(staging)
    staging.mkdir()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"Querying BODS for {NOC} timetable datasets...")
    try:
        datasets = list_datasets(session)
    except requests.RequestException as exc:
        discard_directory(staging)
        print(f"ERROR: dataset listing failed: {safe_error(exc)}")
        return 1
    if not datasets:
        discard_directory(staging)
        print("ERROR: BODS returned no FBRI timetable datasets.")
        return 1
    print(f"Found {len(datasets)} dataset(s) for {NOC}.\n")

    saved = 0
    all_found = set()
    for ds in datasets:
        ds_id = ds.get("id")
        name = (ds.get("name") or "").strip()
        dl = ds.get("url") or f"https://data.bus-data.dft.gov.uk/timetable/dataset/{ds_id}/download/"
        separator = "&" if "?" in dl else "?"
        out = staging / f"fbri_{ds_id}.zip"
        try:
            size = download_archive(
                session, f"{dl}{separator}api_key={API_KEY}", out)
        except RuntimeError as exc:
            discard_directory(staging)
            print(f"  [{ds_id}] download failed: {exc}")
            return 1
        found = scan_txc_for_lines(out)
        all_found |= found
        saved += 1
        flag = f"  <-- has 42-45: {sorted(found)}" if found else ""
        print(f"  [{ds_id}] {name[:50]:50}  {size//1024:>6} KB{flag}")

    if saved != len(datasets):
        discard_directory(staging)
        print("ERROR: not every listed FBRI dataset was saved.")
        return 1
    try:
        publish_directory(staging, OUT_DIR)
    except OSError as exc:
        discard_directory(staging)
        print(f"ERROR: could not publish complete FBRI source set: {exc}")
        return 1
    print(f"\nSaved {saved} TXC zips to {OUT_DIR}")
    print(f"Routes 42-45 found across First's TransXChange: {sorted(all_found) or 'NONE'}")
    if all_found:
        print("Confirmed: the data IS in First's TXC. Next step is converting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
