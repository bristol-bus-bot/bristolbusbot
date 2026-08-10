#!/usr/bin/env python3
"""Build a candidate-only WECA stop-locality lookup from one timetable.

The production command requires explicit live, candidate, report and timetable
paths.  It never writes the live locality file.  A separate fixed-path staging
helper and the shared enrichment promotion transaction own publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from enrichment_contracts import compare_localities, validate_localities
except ModuleNotFoundError:  # Direct repository invocation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
    from enrichment_contracts import compare_localities, validate_localities


WECA_BBOX = (51.2731, 51.6773, -3.1151, -2.2521)
AUTHORITY_NAMES = {
    "E06000022": "Bath and North East Somerset",
    "E06000023": "Bristol",
    "E06000024": "North Somerset",
    "E06000025": "South Gloucestershire",
}
BOUNDARY_EDITION = "December 2025"
BOUNDARY_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "WD_DEC_2025_UK_BSC/FeatureServer/0/query"
)
USER_AGENT = (
    "BristolBusBot/1.0 locality-refresh "
    "(+https://github.com/bristol-bus-bot/bristolbusbot)"
)
MAXIMUM_LOCALITY_BYTES = 16 * 1024 * 1024
MAXIMUM_BOUNDARY_BYTES = 16 * 1024 * 1024
EXPECTED_WARD_FEATURES = 130


class LocalityBuildError(RuntimeError):
    """A complete, safe locality candidate could not be produced."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def regular_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        raw = path.read_bytes()
    except OSError as exc:
        raise LocalityBuildError(f"{label} is unavailable") from exc
    if not raw or len(raw) > maximum:
        raise LocalityBuildError(f"{label} has an unsafe size")
    return raw


def read_live(path: Path) -> tuple[bytes, dict, dict[str, object]]:
    raw = regular_bytes(path, MAXIMUM_LOCALITY_BYTES, "live locality file")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalityBuildError("live locality file is invalid JSON") from exc
    if not isinstance(value, dict):
        raise LocalityBuildError("live locality file is not an object")
    try:
        summary = validate_localities(raw)
    except Exception as exc:
        raise LocalityBuildError(f"live locality file failed validation: {exc}") from exc
    return raw, value, summary


def _sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def load_timetable_stops(path: Path) -> dict[str, dict[str, object]]:
    if path.is_symlink() or not path.is_file():
        raise LocalityBuildError("timetable database is unavailable")
    try:
        connection = sqlite3.connect(_sqlite_uri(path), uri=True)
        try:
            rows = connection.execute(
                "SELECT TRIM(stop_code), stop_name, stop_lat, stop_lon "
                "FROM stops WHERE stop_code IS NOT NULL "
                "AND TRIM(stop_code) <> '' "
                "AND stop_lat BETWEEN ? AND ? "
                "AND stop_lon BETWEEN ? AND ? "
                "ORDER BY TRIM(stop_code), stop_name, stop_lat, stop_lon",
                WECA_BBOX,
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LocalityBuildError(f"timetable stop query failed: {exc}") from exc

    stops: dict[str, dict[str, object]] = {}
    for raw_code, raw_name, raw_lat, raw_lon in rows:
        code = str(raw_code or "").strip()
        name = str(raw_name or "").strip() or code
        try:
            latitude = float(raw_lat)
            longitude = float(raw_lon)
        except (TypeError, ValueError) as exc:
            raise LocalityBuildError(f"stop {code} has invalid coordinates") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise LocalityBuildError(f"stop {code} has invalid coordinates")
        existing = stops.get(code)
        if existing is not None:
            if abs(float(existing["lat"]) - latitude) > 0.000001 \
                    or abs(float(existing["lon"]) - longitude) > 0.000001:
                raise LocalityBuildError(
                    f"stop {code} has conflicting timetable coordinates")
            continue
        stops[code] = {
            "stop_code": code,
            "stop_name": name,
            "lat": latitude,
            "lon": longitude,
        }
    if not stops:
        raise LocalityBuildError("timetable contains no scoped stops")
    return stops


def live_matches_timetable(live: Mapping[str, object],
                           stops: Mapping[str, Mapping[str, object]]) -> bool:
    if set(live) != set(stops):
        return False
    for code, stop in stops.items():
        record = live.get(code)
        if not isinstance(record, dict):
            return False
        if str(record.get("stop_name") or "").strip() != stop["stop_name"]:
            return False
        try:
            if abs(float(record.get("lat")) - float(stop["lat"])) > 0.000001 \
                    or abs(float(record.get("lon")) - float(stop["lon"])) > 0.000001:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _session() -> requests.Session:
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_boundaries() -> tuple[dict, dict[str, object]]:
    codes = ",".join(f"'{code}'" for code in sorted(AUTHORITY_NAMES))
    params = {
        "where": f"LAD25CD IN ({codes})",
        "outFields": "WD25CD,WD25NM,LAD25CD,LAD25NM",
        "outSR": "4326",
        "returnGeometry": "true",
        "f": "geojson",
    }
    try:
        with _session() as session:
            response = session.get(BOUNDARY_URL, params=params, timeout=(10, 90))
            response.raise_for_status()
            raw = response.content
    except requests.RequestException as exc:
        raise LocalityBuildError(f"ONS ward download failed: {exc}") from exc
    if not raw or len(raw) > MAXIMUM_BOUNDARY_BYTES:
        raise LocalityBuildError("ONS ward response has an unsafe size")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalityBuildError("ONS ward response is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise LocalityBuildError("ONS ward response is not GeoJSON")
    features = value.get("features")
    if not isinstance(features, list) or len(features) != EXPECTED_WARD_FEATURES:
        raise LocalityBuildError(
            "ONS ward response did not contain the approved 130 features")
    found: set[str] = set()
    ward_codes: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise LocalityBuildError("ONS ward feature is malformed")
        properties = feature["properties"]
        authority = str(properties.get("LAD25CD") or "")
        ward_code = str(properties.get("WD25CD") or "")
        ward_name = str(properties.get("WD25NM") or "").strip()
        if authority not in AUTHORITY_NAMES or not ward_code or not ward_name:
            raise LocalityBuildError("ONS ward feature is outside the approved scope")
        if ward_code in ward_codes:
            raise LocalityBuildError("ONS ward response contains a duplicate ward")
        ward_codes.add(ward_code)
        found.add(authority)
    if found != set(AUTHORITY_NAMES):
        raise LocalityBuildError("ONS ward response is missing an approved authority")
    return value, {
        "mode": "fetched",
        "edition": BOUNDARY_EDITION,
        "source": BOUNDARY_URL,
        "sha256": digest(raw),
        "bytes": len(raw),
        "feature_count": len(features),
        "authority_codes": sorted(found),
    }


def _point_on_segment(x: float, y: float, a: list, b: list) -> bool:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
    if abs(cross) > 1e-10:
        return False
    return min(ax, bx) - 1e-10 <= x <= max(ax, bx) + 1e-10 \
        and min(ay, by) - 1e-10 <= y <= max(ay, by) + 1e-10


def _ring_relation(x: float, y: float, ring: list) -> int:
    """Return 0 outside, 1 inside, or 2 on a ring boundary."""
    inside = False
    for index in range(len(ring)):
        first = ring[index - 1]
        second = ring[index]
        if _point_on_segment(x, y, first, second):
            return 2
        x1, y1 = float(first[0]), float(first[1])
        x2, y2 = float(second[0]), float(second[1])
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return 1 if inside else 0


def _polygon_covers(x: float, y: float, rings: list) -> bool:
    if not rings or _ring_relation(x, y, rings[0]) == 0:
        return False
    for hole in rings[1:]:
        if _ring_relation(x, y, hole) == 1:
            return False
    return True


def _polygons(geometry: object) -> list[list]:
    if not isinstance(geometry, dict):
        raise LocalityBuildError("ONS ward geometry is malformed")
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "Polygon" and isinstance(coordinates, list):
        return [coordinates]
    if kind == "MultiPolygon" and isinstance(coordinates, list):
        return coordinates
    raise LocalityBuildError("ONS ward geometry type is unsupported")


def boundary_index(boundaries: Mapping[str, object]) -> list[dict[str, object]]:
    wards = []
    for feature in boundaries.get("features", []):
        properties = feature["properties"]
        parts = _polygons(feature.get("geometry"))
        points = [point for polygon in parts for ring in polygon for point in ring]
        if not points:
            raise LocalityBuildError("ONS ward geometry is empty")
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        wards.append({
            "ward_code": str(properties["WD25CD"]),
            "ward_name": str(properties["WD25NM"]),
            "area": AUTHORITY_NAMES[str(properties["LAD25CD"])],
            "parts": parts,
            "bounds": (min(xs), min(ys), max(xs), max(ys)),
        })
    return sorted(wards, key=lambda item: str(item["ward_code"]))


def locate_stop(lat: float, lon: float,
                wards: list[dict[str, object]]) -> list[dict[str, object]]:
    matches = []
    for ward in wards:
        west, south, east, north = ward["bounds"]
        if not (west <= lon <= east and south <= lat <= north):
            continue
        if any(_polygon_covers(lon, lat, rings) for rings in ward["parts"]):
            matches.append(ward)
    return matches


BoundaryLoader = Callable[[], tuple[dict, dict[str, object]]]


def build_localities(stops: Mapping[str, Mapping[str, object]],
                     boundaries: Mapping[str, object]) \
        -> tuple[dict[str, dict[str, object]], int, int]:
    wards = boundary_index(boundaries)
    result: dict[str, dict[str, object]] = {}
    unknown = 0
    ambiguous = 0
    for code, stop in stops.items():
        latitude = float(stop["lat"])
        longitude = float(stop["lon"])
        matches = locate_stop(latitude, longitude, wards)
        if matches:
            selected = matches[0]
            ambiguous += int(len(matches) > 1)
            ward_name = str(selected["ward_name"])
            ward_code = str(selected["ward_code"])
            area = str(selected["area"])
        else:
            unknown += 1
            ward_name = None
            ward_code = None
            area = "Unknown"
        result[code] = {
            "stop_code": code,
            "stop_name": str(stop["stop_name"]),
            "ward_name": ward_name,
            "ward_code": ward_code,
            "area": area,
            "lat": latitude,
            "lon": longitude,
        }
    return result, unknown, ambiguous


def generate_shadow(*, timetable: Path, live: Path, candidate: Path,
                    report: Path, force_boundary_refresh: bool = False,
                    boundary_loader: BoundaryLoader = fetch_boundaries) \
        -> dict[str, object]:
    started_at = utcnow()
    started = time.monotonic()
    live_raw, live_value, live_summary = read_live(live)
    stops = load_timetable_stops(timetable)
    reused = (not force_boundary_refresh
              and live_matches_timetable(live_value, stops))
    if reused:
        candidate_raw = live_raw
        candidate_summary = live_summary
        unknown = int(candidate_summary.get("area_counts", {}).get("Unknown", 0))
        ambiguous = 0
        boundary = {
            "mode": "reused-live",
            "edition": "unchanged",
            "source": "existing validated live artifact",
        }
    else:
        boundary_value, boundary = boundary_loader()
        localities, unknown, ambiguous = build_localities(stops, boundary_value)
        candidate_raw = (json.dumps(
            localities, indent=2, sort_keys=True, ensure_ascii=False,
            allow_nan=False) + "\n").encode()
        candidate_summary = validate_localities(candidate_raw)
        if set(localities) != set(stops):
            raise LocalityBuildError("candidate does not exactly cover timetable stops")
    comparison = compare_localities(candidate_summary, live_summary)
    atomic_bytes(candidate, candidate_raw)
    result: dict[str, object] = {
        "schema": 1,
        "mode": "shadow-only",
        "outcome": "accepted-shadow",
        "candidate_written": True,
        "promotion_attempted": False,
        "started_at": started_at,
        "finished_at": utcnow(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "timetable": {"path": str(timetable), "stops": len(stops)},
        "live": {
            "sha256": digest(live_raw),
            "summary": live_summary,
        },
        "candidate": {
            "sha256": digest(candidate_raw),
            "summary": candidate_summary,
        },
        "coverage": {
            "timetable_stops": len(stops),
            "candidate_stops": int(candidate_summary["records"]),
            "missing": 0,
            "extra": 0,
            "unknown": unknown,
            "ambiguous_boundary": ambiguous,
        },
        "boundary": boundary,
        "comparison": comparison,
    }
    atomic_bytes(
        report,
        (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timetable", type=Path, required=True)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--force-boundary-refresh", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = generate_shadow(
            timetable=args.timetable,
            live=args.live,
            candidate=args.candidate,
            report=args.report,
            force_boundary_refresh=args.force_boundary_refresh,
        )
    except (LocalityBuildError, OSError, ValueError) as exc:
        failure = {
            "schema": 1,
            "mode": "shadow-only",
            "outcome": "rejected",
            "candidate_written": False,
            "promotion_attempted": False,
            "finished_at": utcnow(),
            "error": str(exc)[:500],
        }
        try:
            atomic_bytes(
                args.report,
                (json.dumps(failure, indent=2, sort_keys=True) + "\n").encode(),
            )
        except OSError:
            pass
        parser.exit(1, f"locality shadow rejected: {exc}\n")
    print(json.dumps({
        "status": result["outcome"],
        "candidate": result["candidate"],
        "coverage": result["coverage"],
        "boundary": result["boundary"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
