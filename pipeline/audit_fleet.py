#!/usr/bin/env python3
"""
Fleet enrichment for the audit. Ties a reading's operator + vehicle_ref to the
vehicle's model, propulsion (electric flag / fuel) and livery, from
fbribuses.json. Used by the rollup to break punctuality down by vehicle type and
to build the electric-vs-not and most-used-models views.
"""
import json
import os
import re
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent
FLEET_FILE = HERE / "fbribuses.json"
REPO_FLEET_FILE = HERE.parent / "site" / "fbribuses.json"
PRODUCTION_FLEET_FILE = (
    Path.home() / "bristolbusbot" / "current" / "site" / "fbribuses.json"
)
DIGITS = re.compile(r"(\d+)")


def fleet_path(path=None):
    """Resolve the shared generated fleet cache without coupling it to code."""
    if path is not None:
        return Path(path)
    configured = os.getenv("BBB_FLEET_FILE")
    if configured:
        return Path(configured)
    for candidate in (FLEET_FILE, REPO_FLEET_FILE, PRODUCTION_FLEET_FILE):
        if candidate.is_file():
            return candidate
    return FLEET_FILE


def load_fleet_index(path=None):
    """Return {(operator_noc, fleet_code): {model, electric, fuel}}.
    Keyed by operator so shared fleet numbers across operators don't collide.
    Also indexes by fleet_code alone as a fallback."""
    source = fleet_path(path)
    try:
        with source.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"required audit fleet data could not be loaded from {source}"
        ) from exc
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"required audit fleet data is empty or invalid: {source}")
    index = {}
    for v in data:
        vt = v.get("vehicle_type") or {}
        operator = v.get("operator") or {}
        noc = operator.get("id") if isinstance(operator, dict) else None
        fleet = str(v.get("fleet_code") or v.get("fleet_number") or "").strip()
        if not fleet:
            continue
        entry = {
            "model": vt.get("name") or "Unknown",
            "electric": bool(vt.get("electric")),
            "fuel": vt.get("fuel") or "",
        }
        if noc:
            index[(noc, fleet)] = entry
        index.setdefault(("*", fleet), entry)
    if not index:
        raise RuntimeError(
            f"required audit fleet data contains no usable vehicles: {source}"
        )
    return index


def fleet_number(vehicle_ref):
    if not vehicle_ref:
        return None
    nums = DIGITS.findall(str(vehicle_ref))
    return nums[-1] if nums else None


def fleet_for(index, operator_noc, vehicle_ref):
    fleet = fleet_number(vehicle_ref)
    if not fleet:
        return None
    return index.get((operator_noc, fleet)) or index.get(("*", fleet))
