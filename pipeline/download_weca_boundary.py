#!/usr/bin/env python3
"""
Download and merge WECA boundary GeoJSON from the ONS Open Geography Portal.

WECA (West of England Combined Authority) + North Somerset consists of 4 unitary authorities:
  - E06000022: Bath and North East Somerset
  - E06000023: Bristol, City of
  - E06000024: North Somerset
  - E06000025: South Gloucestershire

This script:
  1. Fetches the generalised (20m) boundaries clipped to coastline (BGC) from ONS
  2. Saves the 4 individual authority polygons as a FeatureCollection
  3. Merges them into a single dissolved polygon (for point-in-polygon testing)
  4. Saves both files for use by the live buses app

Output files:
  - weca_boundary.geojson          : Combined FeatureCollection (4 separate polygons, for map display)
  - weca_boundary_dissolved.geojson : Single merged polygon (for server-side point-in-polygon filtering)

Requirements:
  pip install requests shapely

Attribution:
  Contains National Statistics and OS data © Crown copyright and database right 2024
"""

import json
import logging
import sys
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---

# The 4 WECA unitary authority ONS codes
WECA_AUTHORITY_CODES = ['E06000022', 'E06000023', 'E06000024', 'E06000025']

# ONS ArcGIS API endpoint for Local Authority Districts (BGC = generalised 20m, clipped to coastline)
# BGC is ideal for web maps - small file size but accurate enough for display and point-in-polygon
# Using December 2024 boundaries (latest available)
# If this URL stops working, check: https://geoportal.statistics.gov.uk
# Navigate to: Boundaries > Administrative Boundaries > Local Authority Districts > pick latest BGC
ONS_API_BASE = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Local_Authority_Districts_December_2024_Boundaries_UK_BGC"
    "/FeatureServer/0/query"
)

# Fallback: May 2024 boundaries (in case Dec 2024 isn't available yet)
ONS_API_FALLBACK = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Local_Authority_Districts_May_2024_Boundaries_UK_BGC"
    "/FeatureServer/0/query"
)

# Output directory - same folder as this script (should be the project root)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_COMBINED = SCRIPT_DIR / "weca_boundary.geojson"
OUTPUT_DISSOLVED = SCRIPT_DIR / "weca_boundary_dissolved.geojson"


def fetch_boundaries(api_url: str, codes: list[str]) -> dict | None:
    """
    Fetch boundary GeoJSON for the given LA codes from the ONS ArcGIS API.
    
    The API returns a GeoJSON FeatureCollection with one Feature per authority.
    """
    # Build the WHERE clause: LAD24CD IN ('E06000022','E06000023',...)
    codes_str = ",".join(f"'{c}'" for c in codes)
    where_clause = f"LAD24CD IN ({codes_str})"
    
    params = {
        "where": where_clause,
        "outFields": "LAD24CD,LAD24NM",  # Just the code and name - keeps response smaller
        "outSR": "4326",                  # WGS84 (lat/lon) - what Leaflet expects
        "f": "geojson",                   # GeoJSON format
    }
    
    logger.info(f"Fetching from: {api_url}")
    logger.info(f"  WHERE: {where_clause}")
    
    try:
        response = requests.get(api_url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        # Check for ArcGIS error response (returns 200 but with error object)
        if "error" in data:
            logger.error(f"  ArcGIS API error: {data['error']}")
            return None
        
        features = data.get("features", [])
        logger.info(f"  Got {len(features)} features")
        
        if len(features) != len(codes):
            logger.warning(f"  Expected {len(codes)} features but got {len(features)}")
            found_codes = [f["properties"].get("LAD24CD", "?") for f in features]
            missing = set(codes) - set(found_codes)
            if missing:
                logger.warning(f"  Missing: {missing}")
        
        # Log what we got
        for feature in features:
            props = feature.get("properties", {})
            name = props.get("LAD24NM", "Unknown")
            code = props.get("LAD24CD", "Unknown")
            geom_type = feature.get("geometry", {}).get("type", "Unknown")
            logger.info(f"  ✓ {code}: {name} ({geom_type})")
        
        return data
        
    except requests.exceptions.RequestException as e:
        logger.error(f"  Request failed: {e}")
        return None


def dissolve_boundaries(geojson_data: dict) -> dict:
    """
    Merge all polygons in the FeatureCollection into a single polygon.
    This is used for server-side point-in-polygon testing (is a bus within WECA?).
    
    Uses shapely if available, otherwise falls back to keeping features separate.
    """
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        
        geometries = []
        for feature in geojson_data.get("features", []):
            geom = shape(feature["geometry"])
            geometries.append(geom)
        
        if not geometries:
            logger.error("No geometries to dissolve!")
            return geojson_data
        
        merged = unary_union(geometries)
        logger.info(f"  Dissolved {len(geometries)} polygons into {merged.geom_type}")
        
        dissolved_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "name": "WECA + North Somerset",
                    "authorities": "Bristol, Bath & NE Somerset, South Gloucestershire, North Somerset",
                    "note": "Contains National Statistics and OS data © Crown copyright and database right 2024"
                },
                "geometry": mapping(merged)
            }]
        }
        
        return dissolved_geojson
        
    except ImportError:
        logger.warning("shapely not installed - cannot dissolve boundaries")
        logger.warning("Install with: pip install shapely")
        logger.warning("Saving individual polygons instead (will still work for display, not for point-in-polygon)")
        return geojson_data


def main():
    logger.info("=" * 60)
    logger.info("WECA Boundary Downloader")
    logger.info("=" * 60)
    logger.info(f"Authorities: {', '.join(WECA_AUTHORITY_CODES)}")
    
    # Try primary endpoint first, fall back to alternate
    geojson_data = fetch_boundaries(ONS_API_BASE, WECA_AUTHORITY_CODES)
    
    if geojson_data is None or len(geojson_data.get("features", [])) == 0:
        logger.info("Primary endpoint failed, trying fallback...")
        geojson_data = fetch_boundaries(ONS_API_FALLBACK, WECA_AUTHORITY_CODES)
    
    if geojson_data is None or len(geojson_data.get("features", [])) == 0:
        logger.error("Failed to fetch boundary data from both endpoints!")
        logger.error("You may need to update the API URLs.")
        logger.error("Check: https://geoportal.statistics.gov.uk")
        logger.error("Look for: Local Authority Districts > latest year > BGC (Generalised Clipped)")
        sys.exit(1)
    
    # Save the combined FeatureCollection (4 separate authority polygons).
    # Leaflet displays this on the map; each authority polygon can be styled
    # independently.
    logger.info(f"\nSaving combined boundary to: {OUTPUT_COMBINED}")
    with open(OUTPUT_COMBINED, 'w') as f:
        json.dump(geojson_data, f)
    size_kb = OUTPUT_COMBINED.stat().st_size / 1024
    logger.info(f"  Size: {size_kb:.1f} KB")
    
    # Dissolve into a single polygon for server-side filtering
    logger.info(f"\nDissolving boundaries...")
    dissolved = dissolve_boundaries(geojson_data)
    
    logger.info(f"Saving dissolved boundary to: {OUTPUT_DISSOLVED}")
    with open(OUTPUT_DISSOLVED, 'w') as f:
        json.dump(dissolved, f)
    size_kb = OUTPUT_DISSOLVED.stat().st_size / 1024
    logger.info(f"  Size: {size_kb:.1f} KB")
    
    # Also output the bounding box of the merged area for the SIRI API call
    all_coords = []
    for feature in geojson_data.get("features", []):
        geom = feature.get("geometry", {})
        geom_type = geom.get("type", "")
        coords = geom.get("coordinates", [])
        
        def extract_coords(c):
            """Recursively extract [lon, lat] pairs from nested coordinate arrays"""
            if isinstance(c, list):
                if len(c) >= 2 and isinstance(c[0], (int, float)):
                    all_coords.append(c)
                else:
                    for item in c:
                        extract_coords(item)
        
        extract_coords(coords)
    
    if all_coords:
        lons = [c[0] for c in all_coords]
        lats = [c[1] for c in all_coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        bbox_str = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        
        logger.info(f"\n--- BOUNDING BOX for SIRI API ---")
        logger.info(f"BOUNDING_BOX = '{bbox_str}'")
        logger.info(f"  Min Lon: {min_lon:.6f}  Max Lon: {max_lon:.6f}")
        logger.info(f"  Min Lat: {min_lat:.6f}  Max Lat: {max_lat:.6f}")
        logger.info(f"---------------------------------")
        logger.info(f"Update this in app.py to replace the current BOUNDING_BOX value")
    
    logger.info(f"\n✓ Done!")
    logger.info(f"  {OUTPUT_COMBINED.name}  → load in Leaflet for map display")
    logger.info(f"  {OUTPUT_DISSOLVED.name}  → use server-side for point-in-polygon filtering")


if __name__ == "__main__":
    main()
