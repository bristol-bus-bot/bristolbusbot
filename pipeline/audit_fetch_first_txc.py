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
import io
import sys
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


def list_datasets():
    datasets = []
    url = f"{API}?api_key={API_KEY}&noc={NOC}&limit=100"
    while url:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
        datasets.extend(data.get("results", []))
        url = data.get("next")
    return datasets


def scan_txc_for_lines(zbytes):
    """Return the set of watched route numbers (42-45) present as LineName in a
    TransXChange zip, without a full parse."""
    found = set()
    try:
        with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                text = zf.read(name).decode("utf-8", "ignore")
                for ln in WATCH:
                    if f"<LineName>{ln}</LineName>" in text:
                        found.add(ln)
    except zipfile.BadZipFile:
        pass
    return found


def main():
    if not API_KEY:
        print("ERROR: BODS_API_KEY not found in .env")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Querying BODS for {NOC} timetable datasets...")
    datasets = list_datasets()
    print(f"Found {len(datasets)} dataset(s) for {NOC}.\n")

    saved = 0
    all_found = set()
    for ds in datasets:
        ds_id = ds.get("id")
        name = (ds.get("name") or "").strip()
        dl = ds.get("url") or f"https://data.bus-data.dft.gov.uk/timetable/dataset/{ds_id}/download/"
        try:
            content = requests.get(f"{dl}?api_key={API_KEY}", timeout=120).content
        except Exception as e:
            print(f"  [{ds_id}] download failed: {e}")
            continue
        found = scan_txc_for_lines(content)
        all_found |= found
        out = OUT_DIR / f"fbri_{ds_id}.zip"
        out.write_bytes(content)
        saved += 1
        flag = f"  <-- has 42-45: {sorted(found)}" if found else ""
        print(f"  [{ds_id}] {name[:50]:50}  {len(content)//1024:>6} KB{flag}")

    print(f"\nSaved {saved} TXC zips to {OUT_DIR}")
    print(f"Routes 42-45 found across First's TransXChange: {sorted(all_found) or 'NONE'}")
    if all_found:
        print("Confirmed: the data IS in First's TXC. Next step is converting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
