#!/usr/bin/env python3
"""
Fleet enrichment for the audit. Ties a reading's operator + vehicle_ref to the
vehicle's model, propulsion (electric flag / fuel) and livery, from
fbribuses.json. Used by the rollup to break punctuality down by vehicle type and
to build the electric-vs-not and most-used-models views.
"""
import os
import re
import json

HERE = os.path.dirname(os.path.abspath(__file__))
FLEET_FILE = os.path.join(HERE, "fbribuses.json")
DIGITS = re.compile(r"(\d+)")


def load_fleet_index(path=FLEET_FILE):
    """Return {(operator_noc, fleet_code): {model, electric, fuel}}.
    Keyed by operator so shared fleet numbers across operators don't collide.
    Also indexes by fleet_code alone as a fallback."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
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
