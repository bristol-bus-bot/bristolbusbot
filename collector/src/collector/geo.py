"""Distance, bearing and West of England boundary utilities."""
from __future__ import annotations

import json
import logging
import math

logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6371000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2)
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing 0-360 from point 1 to point 2 (fallback when the feed
    doesn't provide Bearing)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)
    x = math.sin(dlon_r) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


class BoundaryFilter:
    """Point-in-polygon test against the dissolved WECA boundary.

    The API bounding box is coarse and pulls in neighbouring-authority
    services that share route numbers; this is the real filter. If shapely or
    the GeoJSON is unavailable the filter degrades to pass-through (bounding
    box only) with a logged warning — never a crash.
    """

    def __init__(self, geojson_path: str | None):
        self._prepared = None
        self._point_cls = None
        if not geojson_path:
            return
        try:
            from shapely.geometry import shape, Point
            from shapely.prepared import prep
            with open(geojson_path) as f:
                gj = json.load(f)
            geom = shape(gj["features"][0]["geometry"])
            self._prepared = prep(geom)
            self._point_cls = Point
            logger.info("boundary loaded (%s)", geom.geom_type)
        except Exception as e:  # noqa: BLE001 - degrade, don't die
            logger.warning("boundary not loaded (%s); bbox-only filtering", e)

    @property
    def active(self) -> bool:
        return self._prepared is not None

    def contains(self, lat: float, lon: float) -> bool:
        if self._prepared is None:
            return True
        return self._prepared.contains(self._point_cls(lon, lat))
