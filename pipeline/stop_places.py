"""The town a stop is in, for showing next to stop names on the audit.

Names like "Triangle", "Interchange" or "Bus Station" mean little on their own,
and several towns share them. The stop enrichment file carries each stop's
NaPTAN locality ("Clevedon", "Midsomer Norton"); this adds it after the name
unless the name already says it.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

DURABLE = Path("/var/lib/bristolbusbot/enrichment/stop_enrichment.json")
REPO_COPY = Path(__file__).resolve().parents[1] / "site" / "stop_enrichment.json"


def load(path: str | Path | None = None, localities_path: str | Path | None = None) -> dict[str, str]:
    """{stop_code: locality}; empty if no enrichment file can be read.

    Newer stops (extra bays, renamed stops) can be missing from the enrichment
    file. They borrow the town of stops with the same name in the same ward,
    then the commonest town in their ward, using the audit's stop geography."""
    towns = _enrichment(path)
    if towns:
        _fill_from_neighbours(towns, localities_path)
    return towns


def _fill_from_neighbours(towns: dict[str, str], localities_path) -> None:
    try:
        from audit_geo import localities_path as default_path
        source = Path(localities_path) if localities_path else default_path()
        stops = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return
    if not isinstance(stops, dict):
        return
    by_name, by_ward = defaultdict(Counter), defaultdict(Counter)
    for code, stop in stops.items():
        town = towns.get(code)
        if town and isinstance(stop, dict):
            by_name[(stop.get("stop_name"), stop.get("ward_name"))][town] += 1
            by_ward[stop.get("ward_name")][town] += 1
    for code, stop in stops.items():
        if code in towns or not isinstance(stop, dict) or not stop.get("ward_name"):
            continue
        guess = by_name.get((stop.get("stop_name"), stop.get("ward_name"))) or by_ward.get(stop.get("ward_name"))
        if guess:
            towns[code] = guess.most_common(1)[0][0]


def _enrichment(path) -> dict[str, str]:
    candidates = [Path(path)] if path else [
        Path(os.environ["BBB_STOP_ENRICHMENT"]) if os.environ.get("BBB_STOP_ENRICHMENT") else None,
        DURABLE, REPO_COPY]
    for candidate in filter(None, candidates):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        return {
            str(code): str(entry["locality"]).strip()
            for code, entry in data.items()
            if isinstance(entry, dict) and str(entry.get("locality") or "").strip()
        }
    return {}


def place(localities: dict[str, str], stop_code: str | None, stop_name: str | None) -> str | None:
    """The stop's town, or None if unknown or already in the stop's name."""
    town = localities.get(str(stop_code or ""))
    if not town:
        return None
    if town.lower() in str(stop_name or "").lower():
        return None
    return town
