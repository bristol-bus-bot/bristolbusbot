#!/usr/bin/env python3
"""
Geographic enrichment for the audit. Maps a stop_code to its WECA area (the
unitary authority: Bristol / Bath & North East Somerset / South Gloucestershire
/ North Somerset) and its electoral ward, from stop_localities.json.

Used by the rollup to break punctuality down by where the buses run.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCALITIES = HERE / "stop_localities.json"
REPO_LOCALITIES = HERE.parent / "site" / "stop_localities.json"


def localities_path(path=None):
    """Resolve the approved stop geography used by the audit.

    Production releases carry their own pinned copy.  The repository checkout
    falls back to the site's canonical copy so local rollups exercise the same
    data without requiring an ignored pipeline cache.
    """
    if path is not None:
        return Path(path)
    configured = os.getenv("BBB_STOP_LOCALITIES")
    if configured:
        return Path(configured)
    if LOCALITIES.is_file():
        return LOCALITIES
    if REPO_LOCALITIES.is_file():
        return REPO_LOCALITIES
    return LOCALITIES


def load_geo_index(path=None):
    """Return {stop_code: {'area': ..., 'ward': ...}} plus an upper-cased
    alias for each, so lookups are robust to case differences."""
    source = localities_path(path)
    try:
        with source.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"required audit geography could not be loaded from {source}"
        ) from exc
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"required audit geography is empty or invalid: {source}")
    index = {}
    for code, value in data.items():
        if not code or not isinstance(value, dict):
            continue
        area = value.get("area") or "Unknown"
        ward = value.get("ward_name") or "Unknown"
        entry = {"area": area, "ward": ward}
        index[code] = entry
        index.setdefault(code.upper(), entry)
    if not index:
        raise RuntimeError(f"required audit geography contains no usable stops: {source}")
    return index


def geo_for(index, stop_code):
    if not stop_code:
        return None
    return index.get(stop_code) or index.get(str(stop_code).upper())
