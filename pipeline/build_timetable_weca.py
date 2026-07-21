#!/usr/bin/env python3
"""Build a geographically filtered WECA timetable from regional GTFS data.

Input:
  - itm_south_west_gtfs/  (GTFS data from BODS - "Itinerary South West" dataset)
  - weca_boundary_dissolved.geojson  (from download_weca_boundary.py)

Output:
  - timetable_weca.db  (SQLite database, probably 400-800MB depending on operator count)

Requirements:
  pip install shapely

Usage:
  python build_timetable_weca.py

  Or specify paths:
  python build_timetable_weca.py --gtfs /path/to/itm_south_west_gtfs --boundary /path/to/weca_boundary_dissolved.geojson
"""

import os
import sys
import csv
import json
import sqlite3
import logging
import argparse
import shutil
import zipfile
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('build_timetable_weca.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Paths ---
SCRIPT_DIR = Path(__file__).parent
DEFAULT_GTFS_PATH = SCRIPT_DIR / "itm_south_west_gtfs"
DEFAULT_BOUNDARY_PATH = SCRIPT_DIR / "weca_boundary_dissolved.geojson"
OUTPUT_DB = "timetable_weca.db"

# BODS South West GTFS download URL.
GTFS_URL = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/south_west/"


# ============================================================================
# GTFS download
# ============================================================================

def download_and_extract_gtfs(gtfs_path: Path) -> bool:
    """Download fresh GTFS data from BODS and extract to gtfs_path."""
    logger.info(f"Downloading fresh GTFS data from BODS...")
    logger.info(f"  URL: {GTFS_URL}")
    logger.info(f"  Target: {gtfs_path}")

    try:
        # Clean out old data
        if gtfs_path.exists():
            shutil.rmtree(gtfs_path)
        gtfs_path.mkdir(parents=True, exist_ok=True)

        # Download
        response = requests.get(GTFS_URL, stream=True, timeout=120)
        response.raise_for_status()

        zip_path = gtfs_path / "data.zip"
        total = 0
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                total += len(chunk)

        size_mb = total / (1024 * 1024)
        logger.info(f"  Downloaded {size_mb:.1f} MB")

        # Extract
        logger.info("  Extracting zip file...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(gtfs_path)

        # Clean up zip
        zip_path.unlink()

        # Verify key files exist
        required = ['stops.txt', 'routes.txt', 'agency.txt', 'trips.txt',
                     'stop_times.txt', 'calendar.txt']
        for fname in required:
            if not (gtfs_path / fname).exists():
                logger.error(f"  Missing required file after extraction: {fname}")
                return False

        logger.info(f"  GTFS data ready ({len(list(gtfs_path.iterdir()))} files)")
        return True

    except Exception as e:
        logger.error(f"  Download failed: {e}")
        return False

# How many stops a route must have inside the WECA boundary to be included.
# Setting this to 1 is inclusive (catches routes that just clip the edge).
# Setting it to 2+ would be stricter but might miss some legitimate routes.
MIN_STOPS_IN_AREA = 1


# ============================================================================
# Point-in-polygon testing
# ============================================================================

def load_boundary_polygon(boundary_path: Path):
    """
    Load the WECA boundary and return a shapely geometry for point-in-polygon testing.
    Falls back to a simple bounding box if shapely isn't available.
    """
    logger.info(f"Loading boundary from: {boundary_path}")
    
    with open(boundary_path, 'r') as f:
        geojson = json.load(f)
    
    features = geojson.get("features", [])
    if not features:
        logger.error("No features in boundary GeoJSON!")
        sys.exit(1)
    
    try:
        from shapely.geometry import shape, MultiPolygon
        from shapely.ops import unary_union
        from shapely.prepared import prep
        
        geometries = [shape(f["geometry"]) for f in features]
        merged = unary_union(geometries)
        prepared = prep(merged)  # Prepared geometry is much faster for repeated contains() calls
        
        bounds = merged.bounds  # (minx, miny, maxx, maxy) = (min_lon, min_lat, max_lon, max_lat)
        logger.info(f"  Loaded shapely polygon: {merged.geom_type}")
        logger.info(f"  Bounds: lon [{bounds[0]:.4f}, {bounds[2]:.4f}], lat [{bounds[1]:.4f}, {bounds[3]:.4f}]")
        
        return prepared, bounds
        
    except ImportError:
        logger.error("shapely is REQUIRED for geographic filtering.")
        logger.error("Install with: pip install shapely")
        logger.error("")
        logger.error("Without shapely, we can't determine which stops fall within the WECA boundary.")
        logger.error("This is essential for including the right operators and routes.")
        sys.exit(1)


def find_stops_in_area(stops_file: Path, boundary_prepared, bounds) -> set:
    """
    Read all stops from the GTFS stops.txt and return the set of stop_ids
    that fall within the WECA boundary polygon.
    
    Uses the bounding box as a fast pre-filter, then shapely for precise testing.
    """
    from shapely.geometry import Point
    
    min_lon, min_lat, max_lon, max_lat = bounds
    
    logger.info(f"Scanning stops for those within WECA boundary...")
    
    total_stops = 0
    in_bbox = 0
    in_polygon = 0
    stops_in_area = set()
    
    with open(stops_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_stops += 1
            
            try:
                lat = float(row.get('stop_lat', 0))
                lon = float(row.get('stop_lon', 0))
            except (ValueError, TypeError):
                continue
            
            # Fast bounding box pre-filter (eliminates most stops cheaply)
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
            in_bbox += 1
            
            # Precise point-in-polygon test
            point = Point(lon, lat)
            if boundary_prepared.contains(point):
                in_polygon += 1
                stops_in_area.add(row['stop_id'])
    
    logger.info(f"  Total stops in GTFS: {total_stops:,}")
    logger.info(f"  Within bounding box: {in_bbox:,}")
    logger.info(f"  Within WECA polygon: {in_polygon:,}")
    
    return stops_in_area


# ============================================================================
# Route/Trip identification
# ============================================================================

def find_routes_serving_area(stop_times_file: Path, trips_file: Path,
                             stops_in_area: set) -> tuple[set[str], set[str]]:
    """
    Determine which routes serve the WECA area.
    
    Strategy:
    1. Scan stop_times.txt to find trip_ids that visit stops within the area
    2. Scan trips.txt to find route_ids for those trips
    3. Return the set of route_ids AND trip_ids to include
    
    A route is included if it has at least MIN_STOPS_IN_AREA stops within the boundary.
    But we include ALL trips for that route (even if a specific trip variant goes elsewhere),
    because the schedule matching needs complete trip data.
    """
    
    # Step 1: Find trips that visit WECA-area stops
    logger.info(f"Scanning stop_times.txt for trips visiting WECA-area stops...")
    trip_stop_counts = {}  # trip_id -> count of stops in WECA area
    
    total_stop_times = 0
    with open(stop_times_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_stop_times += 1
            if total_stop_times % 5_000_000 == 0:
                logger.info(f"  Scanned {total_stop_times:,} stop_times...")
            
            stop_id = row.get('stop_id', '')
            if stop_id in stops_in_area:
                trip_id = row.get('trip_id', '')
                trip_stop_counts[trip_id] = trip_stop_counts.get(trip_id, 0) + 1
    
    logger.info(f"  Total stop_times scanned: {total_stop_times:,}")
    logger.info(f"  Trips with ≥1 WECA stop: {len(trip_stop_counts):,}")
    
    # Filter to trips with enough stops in area
    qualifying_trip_ids = {
        tid for tid, count in trip_stop_counts.items() 
        if count >= MIN_STOPS_IN_AREA
    }
    logger.info(f"  Trips with ≥{MIN_STOPS_IN_AREA} WECA stops: {len(qualifying_trip_ids):,}")
    
    # Step 2: Find route_ids for qualifying trips
    logger.info(f"Scanning trips.txt to find route_ids...")
    route_ids = set()
    
    with open(trips_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trip_id = row.get('trip_id', '')
            route_id = row.get('route_id', '')
            if trip_id in qualifying_trip_ids:
                route_ids.add(route_id)
    
    logger.info(f"  Routes serving WECA area: {len(route_ids):,}")
    
    # Step 3: Include ALL trips for qualifying routes (not just the ones we
    # found). Re-scan the cheap trips file instead of retaining the complete
    # regional trip-to-route mapping in RAM.
    all_trip_ids = set()
    with open(trips_file, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('route_id', '') in route_ids:
                all_trip_ids.add(row.get('trip_id', ''))
    all_trip_ids.discard('')
    logger.info(f"  Total trips for qualifying routes: {len(all_trip_ids):,}")
    
    return route_ids, all_trip_ids


# ============================================================================
# Database creation
# ============================================================================

def create_tables(conn):
    """Create the GTFS database tables - same schema as the Bristol-only version"""
    tables = {
        "stops": """
            CREATE TABLE IF NOT EXISTS stops (
                stop_id TEXT PRIMARY KEY,
                stop_code TEXT,
                stop_name TEXT,
                stop_lat REAL,
                stop_lon REAL,
                wheelchair_boarding INTEGER,
                location_type INTEGER DEFAULT 0,
                parent_station TEXT,
                platform_code TEXT
            )
        """,
        "routes": """
            CREATE TABLE IF NOT EXISTS routes (
                route_id TEXT PRIMARY KEY,
                agency_id TEXT,
                route_short_name TEXT,
                route_long_name TEXT,
                route_type INTEGER
            )
        """,
        "agency": """
            CREATE TABLE IF NOT EXISTS agency (
                agency_id TEXT PRIMARY KEY,
                agency_name TEXT,
                agency_url TEXT,
                agency_timezone TEXT,
                agency_lang TEXT,
                agency_phone TEXT,
                agency_noc TEXT
            )
        """,
        "calendar": """
            CREATE TABLE IF NOT EXISTS calendar (
                service_id TEXT PRIMARY KEY,
                monday INTEGER,
                tuesday INTEGER,
                wednesday INTEGER,
                thursday INTEGER,
                friday INTEGER,
                saturday INTEGER,
                sunday INTEGER,
                start_date TEXT,
                end_date TEXT
            )
        """,
        "calendar_dates": """
            CREATE TABLE IF NOT EXISTS calendar_dates (
                service_id TEXT,
                date TEXT,
                exception_type INTEGER
            )
        """,
        "trips": """
            CREATE TABLE IF NOT EXISTS trips (
                trip_id TEXT PRIMARY KEY,
                route_id TEXT,
                service_id TEXT,
                trip_headsign TEXT,
                trip_short_name TEXT,
                direction_id INTEGER,
                block_id TEXT,
                shape_id TEXT,
                wheelchair_accessible INTEGER,
                vehicle_journey_code TEXT
            )
        """,
        "stop_times": """
            CREATE TABLE IF NOT EXISTS stop_times (
                trip_id TEXT,
                arrival_time TEXT,
                departure_time TEXT,
                stop_id TEXT,
                stop_sequence INTEGER,
                stop_headsign TEXT,
                pickup_type INTEGER DEFAULT 0,
                drop_off_type INTEGER DEFAULT 0,
                shape_dist_traveled REAL,
                timepoint INTEGER DEFAULT 1
            )
        """
    }
    
    for name, sql in tables.items():
        conn.execute(sql)
        logger.info(f"  Created table: {name}")


def create_indexes(conn):
    """Create indexes used by schedule lookup queries."""
    indexes = [
        # Core schedule lookup: find trip by journey code + operator
        "CREATE INDEX IF NOT EXISTS idx_trips_vjc ON trips(vehicle_journey_code)",
        "CREATE INDEX IF NOT EXISTS idx_routes_agency ON routes(agency_id)",
        
        # Stop times lookup by trip
        "CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id)",
        "CREATE INDEX IF NOT EXISTS idx_stop_times_trip_seq ON stop_times(trip_id, stop_sequence)",
        
        # Fuzzy matching: find trip by line + time + calendar
        "CREATE INDEX IF NOT EXISTS idx_trips_route_dir ON trips(route_id, direction_id)",
        "CREATE INDEX IF NOT EXISTS idx_trips_service ON trips(service_id)",
        "CREATE INDEX IF NOT EXISTS idx_routes_short_name ON routes(route_short_name)",
        
        # Calendar lookups
        "CREATE INDEX IF NOT EXISTS idx_calendar_dates_service ON calendar_dates(service_id)",
        "CREATE INDEX IF NOT EXISTS idx_calendar_dates_date ON calendar_dates(date)",
        
        # Stop lookup
        "CREATE INDEX IF NOT EXISTS idx_stops_code ON stops(stop_code)",
        "CREATE INDEX IF NOT EXISTS idx_stops_latlon ON stops(stop_lat, stop_lon)",
        
        # Agency NOC lookup (used by schedule matching to identify operator)
        "CREATE INDEX IF NOT EXISTS idx_agency_noc ON agency(agency_noc)",
    ]
    
    logger.info("Creating indexes...")
    for idx_sql in indexes:
        conn.execute(idx_sql)
    logger.info(f"  Created {len(indexes)} indexes")


def load_csv_filtered(conn, table_name: str, file_path: Path,
                      filter_set: set | None = None,
                      filter_column: str | None = None,
                      *, required: bool = True) -> int:
    """Stream a GTFS CSV into SQLite, optionally retaining selected rows."""
    if not file_path.exists():
        if required:
            raise FileNotFoundError(f"required GTFS file not found: {file_path}")
        logger.info(f"Optional GTFS file not found: {file_path}")
        return 0

    if filter_set is not None and not filter_column:
        raise ValueError("filter_column is required when filter_set is supplied")
    filter_desc = (f" (filtered by {filter_column})"
                   if filter_set is not None else "")
    logger.info(f"Loading {file_path.name} into {table_name}{filter_desc}...")

    total = 0
    loaded = 0
    batch_size = 5000
    batch = []
    with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        if not columns:
            raise ValueError(f"GTFS file has no header: {file_path}")
        placeholders = ','.join('?' for _ in columns)
        sql = (f"INSERT OR REPLACE INTO {table_name} ({','.join(columns)}) "
               f"VALUES ({placeholders})")

        for row in reader:
            total += 1
            if filter_set is not None \
                    and row.get(filter_column or '', '') not in filter_set:
                continue
            batch.append([row.get(col, '') for col in columns])
            loaded += 1
            if len(batch) >= batch_size:
                conn.executemany(sql, batch)
                batch.clear()
                if loaded % 50_000 == 0:
                    logger.info(f"  Loaded {loaded:,} rows...")

        if batch:
            conn.executemany(sql, batch)

    if filter_set is not None:
        percentage = (loaded / total * 100) if total else 0
        logger.info(
            f"  Loaded {loaded:,} of {total:,} rows ({percentage:.1f}%)")
    else:
        logger.info(f"  Loaded {loaded:,} rows")

    if required and loaded == 0:
        raise ValueError(f"required GTFS table {table_name} loaded no rows")
    return loaded


def load_stop_times_filtered(conn, file_path: Path,
                             trip_ids: set[str]) -> tuple[int, set[str]]:
    """
    Load stop_times.txt filtered to only include rows for qualifying trips.
    
    This is the biggest file (often 10M+ rows) so we process it in a streaming
    fashion rather than loading it all into memory.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"required GTFS file not found: {file_path}")
    
    logger.info(f"Loading {file_path.name} (filtered to {len(trip_ids):,} trips)...")
    
    loaded = 0
    total = 0
    batch = []
    columns = None
    used_stop_ids = set()
    
    with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            total += 1
            
            if row.get('trip_id', '') not in trip_ids:
                continue
            
            if columns is None:
                columns = list(row.keys())
            
            batch.append([row.get(col, '') for col in columns])
            stop_id = row.get('stop_id', '')
            if stop_id:
                used_stop_ids.add(stop_id)
            loaded += 1
            
            if len(batch) >= 10000:
                placeholders = ','.join(['?' for _ in columns])
                sql = f"INSERT OR REPLACE INTO stop_times ({','.join(columns)}) VALUES ({placeholders})"
                conn.executemany(sql, batch)
                batch = []
                
                if loaded % 500000 == 0:
                    logger.info(f"  Loaded {loaded:,} stop_times...")
            
            if total % 5_000_000 == 0:
                logger.info(f"  Scanned {total:,} rows, loaded {loaded:,}...")
    
    # Final batch
    if batch and columns:
        placeholders = ','.join(['?' for _ in columns])
        sql = f"INSERT OR REPLACE INTO stop_times ({','.join(columns)}) VALUES ({placeholders})"
        conn.executemany(sql, batch)
    
    if loaded == 0:
        raise ValueError("required GTFS table stop_times loaded no rows")
    percentage = (loaded / total * 100) if total else 0
    logger.info(
        f"  Loaded {loaded:,} of {total:,} stop_times ({percentage:.1f}%)")
    return loaded, used_stop_ids


def verify_database(conn):
    """Print summary stats about the built database"""
    logger.info("\n" + "=" * 60)
    logger.info("DATABASE VERIFICATION")
    logger.info("=" * 60)
    
    queries = {
        "Agencies": "SELECT COUNT(*) FROM agency",
        "Routes": "SELECT COUNT(*) FROM routes",
        "Trips": "SELECT COUNT(*) FROM trips",
        "Stop times": "SELECT COUNT(*) FROM stop_times",
        "Stops": "SELECT COUNT(*) FROM stops",
        "Calendar entries": "SELECT COUNT(*) FROM calendar",
        "Calendar date exceptions": "SELECT COUNT(*) FROM calendar_dates",
    }
    
    for desc, sql in queries.items():
        count = conn.execute(sql).fetchone()[0]
        logger.info(f"  {desc}: {count:,}")
    
    # Show which operators are included
    logger.info("\n  Operators included:")
    cursor = conn.execute("""
        SELECT a.agency_noc, a.agency_name, COUNT(DISTINCT r.route_id) as route_count
        FROM agency a
        JOIN routes r ON a.agency_id = r.agency_id
        GROUP BY a.agency_noc, a.agency_name
        ORDER BY route_count DESC
    """)
    for row in cursor.fetchall():
        noc, name, count = row
        logger.info(f"    {noc or '?':6s} | {name:40s} | {count} routes")
    
    # Show sample routes
    logger.info("\n  Sample routes (first 20):")
    cursor = conn.execute("""
        SELECT r.route_short_name, r.route_long_name, a.agency_name, a.agency_noc
        FROM routes r
        JOIN agency a ON r.agency_id = a.agency_id
        ORDER BY r.route_short_name
        LIMIT 20
    """)
    for row in cursor.fetchall():
        short, long_name, agency, noc = row
        logger.info(f"    {short or '?':6s} | {long_name or '':40s} | {agency} ({noc})")
    
    # Database file size
    size = conn.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()").fetchone()[0]
    logger.info(f"\n  Database size: {size / (1024*1024):.1f} MB")


# ============================================================================
# Main pipeline
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Build WECA-wide timetable database from GTFS data")
    parser.add_argument("--gtfs", type=Path, default=DEFAULT_GTFS_PATH,
                        help=f"Path to GTFS data directory (default: {DEFAULT_GTFS_PATH})")
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY_PATH,
                        help=f"Path to WECA boundary GeoJSON (default: {DEFAULT_BOUNDARY_PATH})")
    parser.add_argument("--output", type=str, default=OUTPUT_DB,
                        help=f"Output database filename (default: {OUTPUT_DB})")
    parser.add_argument("--no-download", action="store_true",
                        help="Skip downloading fresh GTFS data (use existing)")
    args = parser.parse_args()

    gtfs_path = args.gtfs
    boundary_path = args.boundary
    output_path = Path(args.output).resolve()
    
    logger.info("=" * 60)
    logger.info("WECA Timetable Builder")
    logger.info("=" * 60)
    logger.info(f"GTFS data:  {gtfs_path}")
    logger.info(f"Boundary:   {boundary_path}")
    logger.info(f"Output:     {output_path}")
    logger.info(f"Min stops:  {MIN_STOPS_IN_AREA}")
    
    # Download fresh GTFS data unless --no-download
    if not args.no_download:
        if not download_and_extract_gtfs(gtfs_path):
            logger.error("Failed to download GTFS data. Use --no-download to skip.")
            sys.exit(1)

    # Validate inputs
    if not gtfs_path.exists():
        logger.error(f"GTFS directory not found: {gtfs_path}")
        logger.error("Download from: https://data.bus-data.dft.gov.uk/ (Itinerary South West)")
        sys.exit(1)
    
    if not boundary_path.exists():
        logger.error(f"Boundary file not found: {boundary_path}")
        logger.error("Run download_weca_boundary.py first to generate it")
        sys.exit(1)
    
    required_files = ['stops.txt', 'routes.txt', 'agency.txt', 'trips.txt', 
                       'stop_times.txt', 'calendar.txt']
    for fname in required_files:
        if not (gtfs_path / fname).exists():
            logger.error(f"Required GTFS file not found: {gtfs_path / fname}")
            sys.exit(1)
    
    start_time = datetime.now()
    
    # ---- Geographic filtering ----
    logger.info("\n" + "-" * 40)
    logger.info("PHASE 1: Geographic filtering")
    logger.info("-" * 40)
    
    boundary_prepared, bounds = load_boundary_polygon(boundary_path)
    stops_in_area = find_stops_in_area(gtfs_path / "stops.txt", boundary_prepared, bounds)
    
    if not stops_in_area:
        logger.error("No stops found within WECA boundary! Check your boundary file.")
        sys.exit(1)
    
    # ---- Route and trip identification ----
    logger.info("\n" + "-" * 40)
    logger.info("PHASE 2: Route and trip identification")
    logger.info("-" * 40)
    
    route_ids, trip_ids = find_routes_serving_area(
        gtfs_path / "stop_times.txt",
        gtfs_path / "trips.txt",
        stops_in_area
    )
    
    if not route_ids:
        logger.error("No routes found serving the WECA area!")
        sys.exit(1)
    
    # Find agency_ids for qualifying routes (so we only include relevant agencies)
    logger.info("Identifying agencies for qualifying routes...")
    agency_ids = set()
    with open(gtfs_path / "routes.txt", 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('route_id', '') in route_ids:
                agency_ids.add(row.get('agency_id', ''))
    logger.info(f"  Agencies to include: {len(agency_ids):,}")
    
    # Find service_ids for qualifying trips (so we only include relevant calendar entries)
    logger.info("Identifying service_ids for qualifying trips...")
    service_ids = set()
    with open(gtfs_path / "trips.txt", 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('trip_id', '') in trip_ids:
                service_ids.add(row.get('service_id', ''))
    logger.info(f"  Service IDs to include: {len(service_ids):,}")
    
    # ---- Database build ----
    logger.info("\n" + "-" * 40)
    logger.info("PHASE 3: Building database")
    logger.info("-" * 40)
    
    # Remove existing output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        logger.info(f"Removing existing {output_path}")
        output_path.unlink()
    Path(f"{output_path}-wal").unlink(missing_ok=True)
    Path(f"{output_path}-shm").unlink(missing_ok=True)

    # This is a disposable candidate. Crash safety comes from keeping it
    # separate from the live database until final validation and atomic
    # promotion.
    conn = sqlite3.connect(output_path)
    conn.execute("PRAGMA journal_mode=OFF;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute("PRAGMA cache_size=-32768;")
    conn.execute("PRAGMA temp_store=FILE;")
    
    try:
        create_tables(conn)
        
        # Load data with appropriate filters
        # Agencies: only those with routes in the area
        load_csv_filtered(conn, "agency", gtfs_path / "agency.txt", 
                          agency_ids, "agency_id")
        
        # Routes: only those serving the area
        load_csv_filtered(conn, "routes", gtfs_path / "routes.txt",
                          route_ids, "route_id")
        
        # Trips: only for qualifying routes
        load_csv_filtered(conn, "trips", gtfs_path / "trips.txt",
                          trip_ids, "trip_id")
        
        # Calendar: only for qualifying service_ids
        load_csv_filtered(conn, "calendar", gtfs_path / "calendar.txt",
                          service_ids, "service_id")
        
        # Calendar dates: only for qualifying service_ids
        load_csv_filtered(conn, "calendar_dates", gtfs_path / "calendar_dates.txt",
                          service_ids, "service_id", required=False)
        
        # Stream stop_times to keep memory use bounded.
        _, used_stop_ids = load_stop_times_filtered(
            conn, gtfs_path / "stop_times.txt", trip_ids)

        # Load only stops referenced by retained stop_times instead of loading
        # the full regional set and deleting most of it afterwards.
        load_csv_filtered(conn, "stops", gtfs_path / "stops.txt",
                          used_stop_ids, "stop_id")
        
        # Create indexes
        create_indexes(conn)
        
        conn.commit()
        
        # Verify
        verify_database(conn)
        
    except Exception as e:
        logger.error(f"Database build failed: {e}")
        raise
    finally:
        conn.close()
    
    duration = datetime.now() - start_time
    logger.info(f"\n✓ Build complete in {duration}")
    logger.info(f"  Output: {output_path}")
    logger.info(f"  Size: {output_path.stat().st_size / (1024*1024):.1f} MB")
    logger.info(f"\nNext steps:")
    logger.info(f"  1. Finalize and validate {output_path} before promotion")
    logger.info(f"  2. Update app.py to remove FBRI-only operator filtering")
    logger.info(f"  3. Update app.py BOUNDING_BOX to match the WECA boundary")


if __name__ == "__main__":
    main()
