#!/usr/bin/env python3
"""Download fleet data for all WECA-area operators from bustimes.org API.
Replaces fbribuses.json with data covering all operators visible on the SIRI feed."""

import json
import time
import requests
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent / "fbribuses.json"

# All operators that appear in the WECA GTFS timetable or SIRI feed
# bustimes.org uses the same NOC codes as BODS
OPERATORS = [
    "FBRI",   # First Bristol, Bath & the West
    "SSWL",   # Stagecoach South Wales
    "SDVN",   # Stagecoach South West
    "SCGL",   # Stagecoach West
    "NATX",   # National Express (coaches through Bristol)
    "KEMT",   # Kempsford Transport (CT4N, Y2C etc)
    "VITR",   # Various (Y2C etc — SIRI operator code)
    "FSRV",   # Faresaver
    "NWPT",   # Newport Bus
    "ABUS",   # ABUS
    "BDOL",   # Bakers Dolphin
    "CTCO",   # CT Coaches
    "FRMN",   # FromeBus
    "TDTR",   # Swindon's Bus Company (Thamesdown)
    "EZMT",   # WESTlink
    "PULH",   # Pulhams Coaches
    # Additional operators observed serving WECA routes:
    "FLIX",   # FlixBus
    "EUTX",   # Eurocoaches (they have a yard in Bedminster, we checked)
    "TYSW",   # Taylors Travel
    "COAC",   # Coachstyle
    "LTRV",   # Libra Travel
    # Not on bustimes.org: LEMB (Big Lemon)
]

def fetch_operator_vehicles(operator_code: str) -> list:
    """Fetch all vehicles for an operator, handling pagination."""
    vehicles = []
    url = f"https://bustimes.org/api/vehicles/?operator={operator_code}&format=json&limit=100"
    page = 0

    while url:
        page += 1
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            # Filter out withdrawn vehicles
            active = [v for v in results if not v.get("withdrawn")]
            vehicles.extend(active)

            url = data.get("next")
            if url:
                time.sleep(0.3)  # be polite to the API

        except requests.RequestException as e:
            print(f"  ERROR fetching {operator_code} page {page}: {e}")
            break

    return vehicles


def main():
    all_vehicles = []

    for op in OPERATORS:
        print(f"Fetching {op}...", end=" ", flush=True)
        vehicles = fetch_operator_vehicles(op)
        print(f"{len(vehicles)} active vehicles")
        all_vehicles.extend(vehicles)

    print(f"\nTotal: {len(all_vehicles)} active vehicles across {len(OPERATORS)} operators")

    # Write output
    OUTPUT_PATH.write_text(json.dumps(all_vehicles, indent=2), encoding="utf-8")
    print(f"Written to {OUTPUT_PATH}")

    # Summary by operator
    by_op = {}
    for v in all_vehicles:
        op_name = (v.get("operator") or {}).get("name", "Unknown")
        by_op[op_name] = by_op.get(op_name, 0) + 1
    print("\nBreakdown:")
    for name, count in sorted(by_op.items(), key=lambda x: -x[1]):
        print(f"  {count:4d}  {name}")


if __name__ == "__main__":
    main()
