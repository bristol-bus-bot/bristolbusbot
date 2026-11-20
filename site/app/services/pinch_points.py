"""Pinch points for the live map (planning #145).

The nightly audit already finds road sections where buses regularly take
longer than the timetable allows: segments/<operator>.json, kind "tight"
(the median journey loses more than 2 minutes, at least 30 journeys, steady
in 3 of 4 weeks; see pipeline/SEGMENT_ANALYSIS.md). This turns those files
into map lines. It reads files and joins them to stop positions and route
shapes already held in memory; no database work beyond the cached shape and
stop lists, and the result is rebuilt only when a segment file changes.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
MAX_AGE = timedelta(days=3)
BANDS = ("early", "am_peak", "interpeak", "pm_peak", "evening")
DAYS = ("weekday", "weekend")
SNAP_LIMIT_M = 150         # a stop further than this from the shape: draw it straight


def _metres(a, b) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def _nearest(points, target) -> tuple[int, float]:
    best, best_d = 0, float("inf")
    for index, point in enumerate(points):
        d = _metres(point, target)
        if d < best_d:
            best, best_d = index, d
    return best, best_d


def cut_shape(variants: list[list], start, end) -> list | None:
    """The part of a route shape between two stops, or None if none fits."""
    best = None
    for points in variants:
        if len(points) < 2:
            continue
        i, di = _nearest(points, start)
        j, dj = _nearest(points, end)
        if i >= j or di > SNAP_LIMIT_M or dj > SNAP_LIMIT_M:
            continue
        score = di + dj
        if best is None or score < best[0]:
            best = (score, [list(start), *points[i + 1:j], list(end)])
    return best[1] if best else None


def route_number(label: str) -> str:
    """'1 Bristol' -> '1': the audit adds a place where a number is shared."""
    return (label or "").split(" ", 1)[0]


class PinchPoints:
    def __init__(self, audit_site_dir: str,
                 stops: Callable[[], dict[str, tuple[float, float, str]]],
                 shapes: Callable[[], dict[tuple[str, str, int], list[list]]]):
        self.directory = Path(audit_site_dir) / "segments"
        self._stops = stops
        self._shapes = shapes
        self._fingerprint: tuple | None = None
        self._built: dict | None = None

    def _files(self) -> list[Path]:
        try:
            return sorted(p for p in self.directory.glob("*.json") if p.stem != "ALL")
        except OSError:
            return []

    def _current(self) -> list[tuple[Path, int]]:
        now = datetime.now(timezone.utc)
        fresh = []
        for path in self._files():
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - datetime.fromtimestamp(stat.st_mtime, timezone.utc) <= MAX_AGE:
                fresh.append((path, stat.st_mtime_ns))
        return fresh

    def _build(self, files: list[tuple[Path, int]]) -> dict:
        stops = self._stops()
        shapes = self._shapes()
        groups: dict[tuple, dict] = {}
        window, dropped, sections = None, 0, 0
        for path, _ in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                routes = payload["routes"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("pinch points: %s unreadable: %s", path.name, exc)
                continue
            window = payload.get("window") or window
            operator = path.stem
            for label, items in routes.items():
                for item in items or []:
                    if item.get("kind") != "tight":
                        continue
                    sections += 1
                    a, b = stops.get(item.get("from_stop")), stops.get(item.get("to_stop"))
                    if not a or not b:
                        dropped += 1
                        continue
                    key = (item["from_stop"], item["to_stop"], item["band"], item["day_type"])
                    group = groups.get(key)
                    if group is None:
                        group = groups[key] = {
                            "from": item.get("from") or a[2], "to": item.get("to") or b[2],
                            "from_place": item.get("from_place"), "to_place": item.get("to_place"),
                            "band": item["band"], "day": item["day_type"],
                            "start": (a[0], a[1]), "end": (b[0], b[1]),
                            "line": None, "routes": []}
                    if group["line"] is None:
                        variants = shapes.get((route_number(label), operator, item.get("direction")), [])
                        group["line"] = cut_shape(variants, group["start"], group["end"])
                    group["routes"].append({
                        "route": label, "operator": operator,
                        "timetable_min": item["timetable_min"], "typical_min": item["typical_min"],
                        "lost_min": round(item["typical_min"] - item["timetable_min"], 1),
                        "journeys": item["journeys"]})
        features = {}
        for (_, _, band, day), group in groups.items():
            group["routes"].sort(key=lambda r: -r["lost_min"])
            line = group["line"]
            coordinates = [[lon, lat] for lat, lon in (line or [group["start"], group["end"]])]
            features.setdefault((band, day), []).append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "from": group["from"], "to": group["to"],
                    "from_place": group["from_place"], "to_place": group["to_place"],
                    "approximate": line is None,
                    "lost_min": group["routes"][0]["lost_min"],
                    "routes": group["routes"]}})
        for items in features.values():
            items.sort(key=lambda f: f["properties"]["lost_min"])   # worst drawn last, on top
        return {"window": window, "features": features, "sections": sections, "dropped": dropped}

    def get(self, band: str, day: str) -> dict:
        if band not in BANDS or day not in DAYS:
            return {"available": False, "reason": "unknown_band"}
        files = self._current()
        if not files:
            return {"available": False, "reason": "no_current_data"}
        fingerprint = tuple((p.name, m) for p, m in files)
        if fingerprint != self._fingerprint:
            self._built = self._build(files)
            self._fingerprint = fingerprint
        built = self._built or {}
        features = built.get("features", {}).get((band, day), [])
        counts = {f"{b}:{d}": len(v) for (b, d), v in built.get("features", {}).items()}
        return {"available": True, "band": band, "day": day, "window": built.get("window"),
                "counts": counts, "dropped": built.get("dropped", 0),
                "type": "FeatureCollection", "features": features}
