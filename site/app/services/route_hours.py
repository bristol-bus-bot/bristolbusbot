"""Reader for the audit's hourly files, for the live map's route panel.

The audit export writes hourly/<operator>.json next to the published audit
snapshot every night. This serves one route's slice of it. A missing, broken
or old file simply means no chart; it never breaks the route panel.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_OPERATOR = re.compile(r"^[A-Z0-9]{2,8}$")
MAX_AGE = timedelta(days=3)


class RouteHours:
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self._cache: dict[str, tuple[int, dict]] = {}

    def _load(self, operator: str) -> dict | None:
        path = self.directory / f"{operator}.json"
        try:
            stat = path.stat()
        except OSError:
            return None
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified > MAX_AGE:
            return None
        cached = self._cache.get(operator)
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload.get("routes"), dict) or not isinstance(payload.get("window"), dict):
                raise ValueError("unexpected shape")
        except (OSError, ValueError) as exc:
            logger.warning("hourly file %s unreadable: %s", path.name, exc)
            return None
        self._cache[operator] = (stat.st_mtime_ns, payload)
        return payload

    def route(self, operator: str, route: str) -> dict | None:
        if not _OPERATOR.match(operator or "") or not route:
            return None
        payload = self._load(operator)
        if not payload:
            return None
        hours = payload["routes"].get(route)
        journeys = (payload.get("journeys") or {})
        journey_route = (journeys.get("routes") or {}).get(route)
        if not hours and not journey_route:
            return None
        return {
            "operator": operator, "route": route,
            "window": payload["window"],
            "min_readings": payload.get("min_readings", 30),
            "hours": hours,
            "journeys": journey_route,
            "duty_checked": bool(journeys.get("duty_checked")),
        }
