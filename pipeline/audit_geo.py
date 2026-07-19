#!/usr/bin/env python3
"""
Geographic enrichment for the audit. Maps a stop_code to its WECA area (the
unitary authority: Bristol / Bath & North East Somerset / South Gloucestershire
/ North Somerset) and its electoral ward, from stop_localities.json.

Used by the rollup to break punctuality down by where the buses run.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
LOCALITIES = os.path.join(HERE, "stop_localities.json")


def load_geo_index(path=LOCALITIES):
    """Return {stop_code: {'area': ..., 'ward': ...}} plus an upper-cased
    alias for each, so lookups are robust to case differences."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    index = {}
    for code, value in data.items():
        if not code:
            continue
        area = value.get("area") or "Unknown"
        ward = value.get("ward_name") or "Unknown"
        entry = {"area": area, "ward": ward}
        index[code] = entry
        index.setdefault(code.upper(), entry)
    return index


def geo_for(index, stop_code):
    if not stop_code:
        return None
    return index.get(stop_code) or index.get(str(stop_code).upper())
