"""Each operator's data quality from the BODS Data Catalogue (planning #134).

Private summary, not published. Filters the catalogue to registered services
whose local transport authority includes WECA or North Somerset, in scope and
in season, and counts per operator what BODS itself flags. The catalogue is a
public download (no login): https://data.bus-data.dft.gov.uk/catalogue/
redirects to a fresh ZIP, regenerated regularly.

    python pipeline/bods_catalogue_quality.py <catalogue.zip> [--timetable timetable.db] [-o out.json]

Read the caveats in the output before quoting any figure: BODS's registration
matching is unreliable for our area, its location-matching score is a small
daily sample, and "no published timetable" often means a stale registration.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

AUTHORITIES = {"West of England Combined Authority", "North Somerset Council"}
CAVEATS = [
    "WECA's registration feed is a known BODS problem (missing or wrong cancellations), "
    "so 'no published timetable' often means a registration that has ended but was never "
    "marked cancelled.",
    "BODS's registration matching is unreliable here: some operators show more services "
    "published than registered.",
    "The location-matching score is a small sample (20 random buses per feed per day) and "
    "counts a bus only if every field agrees.",
    "BODS wrongly fails some overnight checks between 00:00 and 05:00 (a known issue).",
]
EVENT_WORDS = re.compile(r"excursion|tour|event|express|festival|park and ride|school|works",
                         re.IGNORECASE)


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    with archive.open(name) as handle:
        return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig")))


def in_area(row: dict) -> bool:
    authorities = set(str(row.get("Local Transport Authority") or "").split("|"))
    return bool(authorities & AUTHORITIES) and row.get("Scope Status") == "In Scope" \
        and row.get("Seasonal Status") != "Out of Season"


def live_route_numbers(timetable: Path | None) -> dict[str, set[str]]:
    if timetable is None:
        return {}
    connection = sqlite3.connect(f"file:{timetable}?mode=ro", uri=True)
    try:
        numbers: dict[str, set[str]] = defaultdict(set)
        for noc, route in connection.execute(
                "SELECT DISTINCT a.agency_noc, r.route_short_name FROM routes r "
                "JOIN agency a USING(agency_id)"):
            numbers[str(noc)].add(str(route).upper())
        return numbers
    finally:
        connection.close()


def unpublished_kind(row: dict, live: dict[str, set[str]]) -> str:
    """A first sort of a 'no published timetable' service, to check by hand."""
    service = str(row.get("Registration:Service Number") or "").upper()
    description = str(row.get("Registration:Service Type Description") or "")
    noc = str(row.get("TXC:NOC") or "")
    if EVENT_WORDS.search(description):
        return "event, tour, school or works service"
    numbers = set().union(*live.values()) if not noc else live.get(noc, set())
    if any(part.strip() in numbers for part in service.split("|")):
        return "route runs in our timetable (registration likely stale or mismatched)"
    return "not in our timetable (cancelled or never published)"


def summarise(catalogue: Path, timetable: Path | None = None) -> dict:
    with zipfile.ZipFile(catalogue) as archive:
        services = [row for row in _rows(archive, "overall_compliance_report.csv") if in_area(row)]
        location = _rows(archive, "location_data_catalogue.csv")
    live = live_route_numbers(timetable)
    scores: dict[str, list[float]] = defaultdict(list)
    for row in location:
        try:
            scores[row["Organisation Name"]].append(
                float(str(row["% AVL to Timetables feed matching score"]).rstrip("%")))
        except (KeyError, ValueError):
            continue
    operators: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    unpublished: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in services:
        name = row.get("Organisation Name") or row.get("Registration:Operator Name") or "Unknown"
        counts = operators[name]
        counts["services"] += 1
        counts["requires_attention"] += row.get("Requires Attention") == "Yes"
        counts["timetable_problem"] += row.get("Timetables requires attention") == "Yes"
        counts["timetable_unpublished"] += row.get("Timetables Published Status") == "Unpublished"
        counts["look_ahead_short"] += row.get("Timetables Timeliness Status") == "42 day look ahead is incomplete"
        counts["location_problem"] += row.get("AVL requires attention") == "Yes"
        counts["location_unpublished"] += row.get("AVL Published Status") == "No"
        counts["location_matching_error"] += row.get("Error in AVL to Timetable Matching") == "Yes"
        counts["fares_problem"] += row.get("Fares requires attention") == "Yes"
        if row.get("Timetables Published Status") == "Unpublished":
            unpublished[name][unpublished_kind(row, live)] += 1
    total: dict[str, int] = defaultdict(int)
    for counts in operators.values():
        for key, value in counts.items():
            total[key] += value
    return {
        "catalogue": catalogue.name,
        "catalogue_date": datetime.fromtimestamp(catalogue.stat().st_mtime, timezone.utc).date().isoformat(),
        "area": sorted(AUTHORITIES),
        "caveats": CAVEATS,
        "total": dict(total),
        "operators": {
            name: {**dict(counts),
                   "location_matching_score_pct": (round(sum(scores[name]) / len(scores[name]), 1)
                                                   if scores.get(name) else None),
                   "unpublished_by_kind": dict(unpublished.get(name, {}))}
            for name, counts in sorted(operators.items(), key=lambda item: -item[1]["services"])
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("--timetable", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    report = summarise(args.catalogue, args.timetable)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
