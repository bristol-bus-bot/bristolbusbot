import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import geocode_stops


def timetable(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE stops (stop_code TEXT, stop_name TEXT, "
        "stop_lat REAL, stop_lon REAL)")
    connection.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def place(code: str, name: str, lat: float, lon: float) -> dict:
    return {
        "stop_code": code,
        "stop_name": name,
        "ward_name": "Old ward",
        "ward_code": "OLD",
        "area": "Bristol",
        "lat": lat,
        "lon": lon,
    }


def boundaries() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "WD25CD": "E05000001",
                "WD25NM": "Test Ward",
                "LAD25CD": "E06000023",
                "LAD25NM": "Bristol, City of",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-2.61, 51.44], [-2.57, 51.44],
                    [-2.57, 51.47], [-2.61, 51.47], [-2.61, 51.44],
                ]],
            },
        }],
    }


def fetched_boundary():
    return boundaries(), {
        "mode": "fetched",
        "edition": geocode_stops.BOUNDARY_EDITION,
        "source": geocode_stops.BOUNDARY_URL,
        "sha256": "a" * 64,
        "bytes": 100,
        "feature_count": 130,
        "authority_codes": sorted(geocode_stops.AUTHORITY_NAMES),
    }


def test_builds_exact_candidate_and_keeps_unknown_scoped_stops(tmp_path):
    database = tmp_path / "timetable.db"
    timetable(database, [
        ("A", "Central", 51.45, -2.59),
        ("B", "East", 51.46, -2.58),
        ("C", "Outside authority", 51.30, -2.30),
    ])
    live = tmp_path / "live.json"
    old_unknown = place("Z", "Old Z", 51.47, -2.57)
    old_unknown.update({
        "ward_name": None, "ward_code": None, "area": "Unknown",
    })
    live.write_text(json.dumps({
        "X": place("X", "Old X", 51.45, -2.59),
        "Y": place("Y", "Old Y", 51.46, -2.58),
        "Z": old_unknown,
    }))
    candidate = tmp_path / "shadow" / "stop_localities.json"
    report = tmp_path / "report.json"

    result = geocode_stops.generate_shadow(
        timetable=database,
        live=live,
        candidate=candidate,
        report=report,
        boundary_loader=fetched_boundary,
    )

    payload = json.loads(candidate.read_text())
    assert set(payload) == {"A", "B", "C"}
    assert payload["A"]["ward_name"] == "Test Ward"
    assert payload["A"]["area"] == "Bristol"
    assert payload["C"]["area"] == "Unknown"
    assert result["coverage"] == {
        "timetable_stops": 3,
        "candidate_stops": 3,
        "missing": 0,
        "extra": 0,
        "unknown": 1,
        "ambiguous_boundary": 0,
    }
    assert result["promotion_attempted"] is False
    assert json.loads(report.read_text())["outcome"] == "accepted-shadow"


def test_reuses_exact_live_bytes_without_network(tmp_path):
    database = tmp_path / "timetable.db"
    timetable(database, [("A", "Central", 51.45, -2.59)])
    live = tmp_path / "live.json"
    live_raw = (json.dumps({
        "A": place("A", "Central", 51.45, -2.59),
    }) + "\n").encode()
    live.write_bytes(live_raw)
    candidate = tmp_path / "candidate.json"
    report = tmp_path / "report.json"

    result = geocode_stops.generate_shadow(
        timetable=database,
        live=live,
        candidate=candidate,
        report=report,
        boundary_loader=lambda: pytest.fail("boundary source must not run"),
    )

    assert candidate.read_bytes() == live_raw
    assert result["boundary"]["mode"] == "reused-live"
    assert result["candidate"]["sha256"] == hashlib.sha256(live_raw).hexdigest()


def test_source_failure_cannot_replace_existing_shadow(tmp_path):
    database = tmp_path / "timetable.db"
    timetable(database, [("A", "Central", 51.45, -2.59)])
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"X": place("X", "Old", 51.45, -2.59)}))
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(b"old-shadow")

    with pytest.raises(geocode_stops.LocalityBuildError, match="source failed"):
        geocode_stops.generate_shadow(
            timetable=database,
            live=live,
            candidate=candidate,
            report=tmp_path / "report.json",
            boundary_loader=lambda: (_ for _ in ()).throw(
                geocode_stops.LocalityBuildError("source failed")),
        )

    assert candidate.read_bytes() == b"old-shadow"


def test_conflicting_duplicate_stop_coordinates_are_rejected(tmp_path):
    database = tmp_path / "timetable.db"
    timetable(database, [
        ("A", "One", 51.45, -2.59),
        ("A", "Two", 51.46, -2.58),
    ])

    with pytest.raises(geocode_stops.LocalityBuildError, match="conflicting"):
        geocode_stops.load_timetable_stops(database)
