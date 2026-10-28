"""Build places.json, the outlines and ward positions behind the audit map.

Council area outlines come from site/weca_boundary.geojson, thinned so the file
stays small. Each ward is placed at the average position of its bus stops in
site/stop_localities.json. Re-run when either file changes:

    python audit-site/build_places.py
"""

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "audit-site" / "places.json"
MAX_POINTS_PER_RING = 400


def short_name(name: str) -> str:
    # The boundary file says "Bristol, City of"; the audit says "Bristol".
    return name.removesuffix(", City of")


def areas() -> list[dict]:
    geojson = json.loads((ROOT / "site/weca_boundary.geojson").read_text(encoding="utf-8"))
    out = []
    for feature in geojson["features"]:
        geometry = feature["geometry"]
        polygons = (geometry["coordinates"] if geometry["type"] == "MultiPolygon"
                    else [geometry["coordinates"]])
        rings = []
        for polygon in polygons:
            ring = polygon[0]
            if len(ring) < 8:
                continue
            step = max(1, len(ring) // MAX_POINTS_PER_RING)
            rings.append([[round(x, 4), round(y, 4)] for x, y in ring[::step]])
        out.append({"name": short_name(feature["properties"]["LAD24NM"]), "rings": rings})
    return sorted(out, key=lambda a: a["name"])


def wards() -> dict[str, list[float]]:
    stops = json.loads((ROOT / "site/stop_localities.json").read_text(encoding="utf-8"))
    points = defaultdict(list)
    for stop in stops.values():
        if stop.get("ward_name") and stop.get("lat") is not None and stop.get("lon") is not None:
            points[stop["ward_name"]].append((stop["lat"], stop["lon"]))
    return {
        ward: [round(sum(p[0] for p in ps) / len(ps), 4), round(sum(p[1] for p in ps) / len(ps), 4)]
        for ward, ps in sorted(points.items())
    }


def build() -> dict:
    return {"areas": areas(), "wards": wards()}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
