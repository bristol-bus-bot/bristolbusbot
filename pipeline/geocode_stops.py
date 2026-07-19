#!/usr/bin/env python3
"""
Geocode all WECA bus stops to ward/locality using ONS ward boundary polygons.
Downloads ward boundaries from ONS if not already cached, then matches each
stop's lat/lon to a ward using point-in-polygon testing.

Output: stop_localities.json (keyed by stop_code)

Usage:
    python geocode_stops.py                 # Download wards + geocode
    python geocode_stops.py --no-download   # Use cached ward files only
"""

import os
import sys
import json
import time
import sqlite3
import argparse
import requests
from pathlib import Path
from collections import defaultdict
from shapely.geometry import shape, Point

SCRIPT_DIR = Path(__file__).parent
TIMETABLE_DB = SCRIPT_DIR / "timetable.db"
BOUNDARIES_DIR = SCRIPT_DIR / "geographic_boundaries"
OUTPUT_JSON = SCRIPT_DIR / "stop_localities.json"

# ONS FeatureServer API (May 2024 Wards)
API_BASE = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Wards_May_2024_Boundaries_UK_BSC/FeatureServer/0/query"

# WECA local authorities and their approximate geographic bounds
LOCAL_AUTHORITIES = {
    'bristol': {
        'name': 'Bristol',
        'output_file': 'bristol_wards.geojson',
        'bounds': {'south': 51.38, 'north': 51.54, 'west': -2.75, 'east': -2.48}
    },
    'bath': {
        'name': 'Bath and North East Somerset',
        'output_file': 'bath_wards.geojson',
        'bounds': {'south': 51.20, 'north': 51.48, 'west': -2.65, 'east': -2.20}
    },
    'north_somerset': {
        'name': 'North Somerset',
        'output_file': 'north_somerset_wards.geojson',
        'bounds': {'south': 51.22, 'north': 51.48, 'west': -3.08, 'east': -2.62}
    },
    'south_glos': {
        'name': 'South Gloucestershire',
        'output_file': 'south_glos_wards.geojson',
        'bounds': {'south': 51.42, 'north': 51.70, 'west': -2.65, 'east': -2.25}
    }
}


# ============================================================================
# Ward boundary download
# ============================================================================

def download_ward_boundaries():
    """Download ward boundary GeoJSON files from ONS Open Geography Portal."""
    BOUNDARIES_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading ward boundaries from ONS...")

    for key, info in LOCAL_AUTHORITIES.items():
        b = info['bounds']
        bbox = f"{b['west']},{b['south']},{b['east']},{b['north']}"
        params = {
            'where': '1=1',
            'geometry': bbox,
            'geometryType': 'esriGeometryEnvelope',
            'spatialRel': 'esriSpatialRelIntersects',
            'outFields': '*',
            'f': 'geojson',
            'returnGeometry': 'true'
        }

        try:
            resp = requests.get(API_BASE, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            ward_count = len(data.get('features', []))

            out_path = BOUNDARIES_DIR / info['output_file']
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            print(f"  {info['name']}: {ward_count} wards -> {info['output_file']}")
        except Exception as e:
            print(f"  ERROR downloading {info['name']}: {e}")

        time.sleep(1)  # Be polite to the API


# ============================================================================
# Ward boundary loading
# ============================================================================

def load_ward_boundaries():
    """Load all ward boundary GeoJSON files into shapely polygons."""
    print("Loading ward boundaries...")
    boundaries = []
    seen_codes = set()

    for key, info in LOCAL_AUTHORITIES.items():
        fpath = BOUNDARIES_DIR / info['output_file']
        if not fpath.exists():
            print(f"  WARNING: {fpath.name} not found, skipping")
            continue

        with open(fpath, 'r', encoding='utf-8') as f:
            geojson = json.load(f)

        count = 0
        for feature in geojson.get('features', []):
            code = feature['properties'].get('WD24CD')
            name = feature['properties'].get('WD24NM')
            if code in seen_codes:
                continue
            seen_codes.add(code)
            boundaries.append({
                'area': info['name'],
                'ward_code': code,
                'ward_name': name,
                'polygon': shape(feature['geometry'])
            })
            count += 1
        print(f"  {info['name']}: {count} wards")

    print(f"  Total: {len(boundaries)} unique wards")
    return boundaries


# ============================================================================
# Stop geocoding
# ============================================================================

def find_ward(lat, lon, boundaries):
    """Find which ward contains a point. Returns dict or None."""
    pt = Point(lon, lat)
    for b in boundaries:
        if b['polygon'].contains(pt):
            return {'ward_name': b['ward_name'], 'ward_code': b['ward_code'], 'area': b['area']}
    return None


def geocode_stops(boundaries):
    """Load all stops from timetable.db, match to wards, write stop_localities.json."""
    if not TIMETABLE_DB.exists():
        print(f"ERROR: {TIMETABLE_DB} not found")
        sys.exit(1)

    conn = sqlite3.connect(str(TIMETABLE_DB))

    # Get ALL stops in the WECA bounding box (not filtered by operator)
    cursor = conn.execute("""
        SELECT DISTINCT stop_code, stop_name, stop_lat, stop_lon
        FROM stops
        WHERE stop_lat BETWEEN 51.2731 AND 51.6773
        AND stop_lon BETWEEN -3.1151 AND -2.2521
        AND stop_code IS NOT NULL AND stop_code != ''
        ORDER BY stop_code
    """)
    stops = cursor.fetchall()
    conn.close()

    print(f"\nGeocoding {len(stops)} stops...")
    localities = {}
    stats = defaultdict(int)
    unmatched = []

    for i, (code, name, lat, lon) in enumerate(stops, 1):
        if i % 500 == 0:
            print(f"  {i}/{len(stops)}...")

        ward = find_ward(lat, lon, boundaries)
        if ward:
            localities[code] = {
                'stop_code': code, 'stop_name': name,
                'ward_name': ward['ward_name'], 'ward_code': ward['ward_code'],
                'area': ward['area'], 'lat': lat, 'lon': lon
            }
            stats[ward['area']] += 1
        else:
            localities[code] = {
                'stop_code': code, 'stop_name': name,
                'ward_name': None, 'ward_code': None,
                'area': 'Unknown', 'lat': lat, 'lon': lon
            }
            stats['Unknown'] += 1
            unmatched.append((code, name, lat, lon))

    # Save
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(localities, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(localities)} stop localities to {OUTPUT_JSON.name}")
    print("\nBreakdown:")
    for area in ['Bristol', 'Bath and North East Somerset', 'South Gloucestershire', 'North Somerset', 'Unknown']:
        c = stats.get(area, 0)
        if c:
            print(f"  {area:35s}: {c:4d} ({c*100/len(localities):.1f}%)")

    if unmatched:
        print(f"\n{len(unmatched)} stops outside ward boundaries:")
        for code, name, lat, lon in unmatched[:10]:
            print(f"  {code:12s} {name:40s} ({lat:.4f}, {lon:.4f})")
        if len(unmatched) > 10:
            print(f"  ... and {len(unmatched) - 10} more")

    return localities


def main():
    parser = argparse.ArgumentParser(description="Geocode WECA bus stops to ward/locality")
    parser.add_argument("--no-download", action="store_true",
                        help="Skip downloading ward boundaries (use cached files)")
    args = parser.parse_args()

    if not args.no_download:
        download_ward_boundaries()

    boundaries = load_ward_boundaries()
    if not boundaries:
        print("ERROR: No ward boundaries loaded")
        sys.exit(1)

    geocode_stops(boundaries)


if __name__ == "__main__":
    main()
