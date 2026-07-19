"""Depot detection: is this position inside a known depot's radius?"""
from __future__ import annotations

import math

DEPOT_LOCATIONS = [
    ("Lawrence Hill",        51.46067, -2.56622, 150),
    ("Hengrove",             51.4205,  -2.5868,  200),
    ("Bath (Weston Island)", 51.3820,  -2.3938,  150),
    ("Weston-super-Mare",    51.3423,  -2.9572,  200),
    ("Keynsham (Gypsy Ln)",  51.3920,  -2.4780,  150),
    # Radius derived from the known yard boundary.
    ("Eurocoaches Yard",     51.45592, -2.56926,  78),
]


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def check_depot(lat: float, lon: float) -> str | None:
    for name, dlat, dlon, radius in DEPOT_LOCATIONS:
        if _haversine_m(lat, lon, dlat, dlon) <= radius:
            return name
    return None
