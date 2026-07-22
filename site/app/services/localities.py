"""Build stop-search data with localities, enrichment and served routes."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .stops import BBOX, all_stops

logger = logging.getLogger(__name__)


def _load_json(path: str) -> dict:
    p = Path(path) if path else None
    if not p or not p.exists():
        if path:
            logger.warning("locality data missing: %s", path)
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("locality load failed %s: %s", path, e)
        return {}


def routes_per_stop(gtfs_conn) -> dict[str, list[str]]:
    """Return routes per stop, with numeric routes sorted first."""
    has_precomputed = gtfs_conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='stop_routes'").fetchone()
    if has_precomputed:
        rows = gtfs_conn.execute(
            "SELECT stop_code, route_short_name FROM stop_routes "
            "ORDER BY stop_code, route_short_name").fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["stop_code"], []).append(
                row["route_short_name"])
        return {
            code: sorted(
                routes,
                key=lambda x: (
                    not x.isdigit(), int(x) if x.isdigit() else 0, x))
            for code, routes in grouped.items()
        }

    # Compatibility for the previous rollback database during the one-time
    # schema transition. All newly generated candidates require stop_routes.
    logger.warning(
        "legacy timetable has no stop_routes table; using slow fallback query")
    rows = gtfs_conn.execute(f"""
        SELECT s.stop_code, GROUP_CONCAT(DISTINCT r.route_short_name) AS routes
        FROM stop_times st
        JOIN stops s ON st.stop_id = s.stop_id
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        WHERE s.stop_lat BETWEEN {BBOX[0]} AND {BBOX[1]}
          AND s.stop_lon BETWEEN {BBOX[2]} AND {BBOX[3]}
          AND s.stop_code IS NOT NULL AND s.stop_code != ''
        GROUP BY s.stop_code""").fetchall()
    return {r["stop_code"]: sorted(
        (r["routes"] or "").split(","),
        key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))
        for r in rows}


def stops_with_locality(gtfs_conn, localities_path: str,
                        enrichment_path: str) -> list[dict]:
    localities = _load_json(localities_path)
    enrichment = _load_json(enrichment_path)
    routes = routes_per_stop(gtfs_conn)
    out = []
    for stop in all_stops(gtfs_conn):
        code = stop["stop_code"]
        if not code:
            continue
        loc = localities.get(code) or {}
        enr = enrichment.get(code) or {}
        out.append({
            "stop_code": code,
            "stop_name": stop["common_name"],
            "lat": stop["latitude"],
            "lon": stop["longitude"],
            "ward": loc.get("ward_name") or "Other",
            "area": loc.get("area") or "",
            "routes": routes.get(code, []),
            "street": enr.get("street") or "",
            "enriched_locality": enr.get("locality") or "",
            "local_authority": "",
        })
    return out
