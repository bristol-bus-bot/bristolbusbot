"""Reported position inside a reviewed yard, not a vehicle's allocated garage."""
from __future__ import annotations

import json
import math
from pathlib import Path

_DATA = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "depot_boundaries.geojson")
    .read_text(encoding="utf-8")
)
_DEPOTS = [
    (feature["properties"]["name"], feature["geometry"]["coordinates"])
    for feature in _DATA["features"]
]


def _in_ring(x: float, y: float, ring: list) -> bool:
    """Ray casting for a closed ring; boundary points count as inside."""
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (abs(cross) <= 1e-15 and min(ax, bx) <= x <= max(ax, bx)
                and min(ay, by) <= y <= max(ay, by)):
            return True
        if (ay > y) != (by > y):
            intersection = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < intersection:
                inside = not inside
    return inside


def check_depot(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    for name, rings in _DEPOTS:
        if (_in_ring(lon, lat, rings[0])
                and not any(_in_ring(lon, lat, hole) for hole in rings[1:])):
            return name
    return None
