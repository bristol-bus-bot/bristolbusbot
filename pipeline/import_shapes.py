#!/usr/bin/env python3
"""
Import GTFS shapes into timetable.db and pre-compute simplified route polylines.
Reads shapes.txt from the busbot GTFS data, filters to Bristol-only routes,
and creates a route_shapes table with simplified polylines for the frontend.
"""

import os
import sys
import csv
import json
import math
import sqlite3
from pathlib import Path
from collections import defaultdict

# Paths can be overridden for a specific timetable build.
DB_PATH = os.getenv("BBB_TIMETABLE_DB",
                    os.path.join(os.path.dirname(__file__), "timetable.db"))
SHAPES_PATH = Path(os.getenv("BBB_GTFS_DIR",
                   Path(__file__).parent / "itm_south_west_gtfs")) / "shapes.txt"

# Douglas-Peucker simplification tolerance (in degrees, ~0.00003 ≈ 3.3m)
# Tighter tolerance = more points retained = better snap accuracy
SIMPLIFY_TOLERANCE = 0.00003


def perpendicular_distance(point, line_start, line_end):
    """Calculate perpendicular distance from a point to a line segment."""
    x, y = point
    x1, y1 = line_start
    x2, y2 = line_end

    dx = x2 - x1
    dy = y2 - y1

    if dx == 0 and dy == 0:
        return math.sqrt((x - x1) ** 2 + (y - y1) ** 2)

    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))

    proj_x = x1 + t * dx
    proj_y = y1 + t * dy

    return math.sqrt((x - proj_x) ** 2 + (y - proj_y) ** 2)


def douglas_peucker(points, tolerance):
    """Simplify a polyline using the Douglas-Peucker algorithm."""
    if len(points) <= 2:
        return points

    # Find the point with maximum distance from the line between first and last
    max_dist = 0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = perpendicular_distance(points[i], points[0], points[-1])
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > tolerance:
        left = douglas_peucker(points[:max_idx + 1], tolerance)
        right = douglas_peucker(points[max_idx:], tolerance)
        return left[:-1] + right
    else:
        return [points[0], points[-1]]


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    if not SHAPES_PATH.exists():
        print(f"ERROR: shapes.txt not found at {SHAPES_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor = conn.cursor()

    # Step 1: Get all shape_ids used by Bristol trips
    print("Step 1: Finding Bristol shape_ids from trips table...")
    cursor.execute("SELECT DISTINCT shape_id FROM trips WHERE shape_id IS NOT NULL AND shape_id != ''")
    bristol_shape_ids = set(row[0] for row in cursor.fetchall())
    print(f"  Found {len(bristol_shape_ids)} unique shape_ids in Bristol trips")

    if not bristol_shape_ids:
        print("ERROR: No shape_ids found in trips table. Cannot import shapes.")
        conn.close()
        sys.exit(1)

    # Step 2: Create shapes table
    print("Step 2: Creating shapes table...")
    cursor.execute("DROP TABLE IF EXISTS shapes")
    cursor.execute("""
        CREATE TABLE shapes (
            shape_id TEXT NOT NULL,
            shape_pt_lat REAL NOT NULL,
            shape_pt_lon REAL NOT NULL,
            shape_pt_sequence INTEGER NOT NULL
        )
    """)

    # Step 3: Read shapes.txt and filter to Bristol shape_ids
    print(f"Step 3: Reading shapes.txt ({SHAPES_PATH})...")
    print("  (This may take a minute - file is ~615MB)")

    inserted = 0
    skipped = 0
    batch = []
    BATCH_SIZE = 50000

    with open(SHAPES_PATH, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            shape_id = row.get('shape_id', '').strip()
            if shape_id not in bristol_shape_ids:
                skipped += 1
                if skipped % 1000000 == 0:
                    print(f"  Processed {skipped + inserted} rows ({inserted} kept, {skipped} skipped)...")
                continue

            try:
                lat = float(row['shape_pt_lat'])
                lon = float(row['shape_pt_lon'])
                seq = int(row['shape_pt_sequence'])
            except (ValueError, KeyError):
                continue

            batch.append((shape_id, lat, lon, seq))
            inserted += 1

            if len(batch) >= BATCH_SIZE:
                cursor.executemany(
                    "INSERT INTO shapes VALUES (?, ?, ?, ?)", batch
                )
                batch.clear()
                print(f"  Inserted {inserted} shape points so far...")

    if batch:
        cursor.executemany("INSERT INTO shapes VALUES (?, ?, ?, ?)", batch)

    conn.commit()
    print(f"  Done: {inserted} Bristol shape points imported, {skipped} non-Bristol skipped")

    # Step 4: Create index
    print("Step 4: Creating index on shapes...")
    cursor.execute("CREATE INDEX idx_shapes_id_seq ON shapes (shape_id, shape_pt_sequence)")
    conn.commit()

    # Step 5: Build simplified route polylines
    print("Step 5: Building simplified route polylines...")

    # Get (route_short_name, operator_noc) -> shape_ids mapping (via trips -> routes -> agency)
    cursor.execute("""
        SELECT DISTINCT r.route_short_name, a.agency_noc, t.shape_id, t.direction_id
        FROM trips t
        JOIN routes r ON t.route_id = r.route_id
        JOIN agency a ON r.agency_id = a.agency_id
        WHERE t.shape_id IS NOT NULL AND t.shape_id != ''
    """)
    route_shapes_map = defaultdict(list)  # (route_name, operator_noc) -> [(shape_id, direction_id), ...]
    for route_name, operator_noc, shape_id, direction_id in cursor.fetchall():
        route_shapes_map[(route_name, operator_noc)].append((shape_id, direction_id or 0))

    print(f"  Found {len(route_shapes_map)} routes with shape data")

    # Create pre-computed table
    cursor.execute("DROP TABLE IF EXISTS route_shapes")
    cursor.execute("""
        CREATE TABLE route_shapes (
            route_name TEXT NOT NULL,
            operator_noc TEXT NOT NULL,
            direction_id INTEGER NOT NULL,
            variant INTEGER NOT NULL DEFAULT 0,
            points_json TEXT NOT NULL,
            PRIMARY KEY (route_name, operator_noc, direction_id, variant)
        )
    """)

    # Helper: compute centroid of a shape
    def get_shape_centroid(cursor, shape_id):
        cursor.execute("SELECT AVG(shape_pt_lat), AVG(shape_pt_lon) FROM shapes WHERE shape_id = ?", (shape_id,))
        row = cursor.fetchone()
        return (row[0], row[1]) if row and row[0] else None

    # Helper: haversine-ish distance in km between two lat/lon points
    def approx_dist_km(p1, p2):
        import math
        dlat = math.radians(p2[0] - p1[0])
        dlon = math.radians(p2[1] - p1[1])
        lat_avg = math.radians((p1[0] + p2[0]) / 2)
        dx = dlon * math.cos(lat_avg)
        return math.sqrt(dlat**2 + dx**2) * 6371

    # Cluster shapes by geographic proximity (>5km apart = different service)
    CLUSTER_DISTANCE_KM = 5.0

    routes_done = 0
    split_count = 0
    for (route_name, operator_noc), shape_entries in route_shapes_map.items():
        # Group by direction
        by_direction = defaultdict(list)
        for shape_id, direction_id in shape_entries:
            by_direction[direction_id].append(shape_id)

        for direction_id, shape_ids in by_direction.items():
            # Get centroid and point count for each shape
            shape_info = []
            for sid in shape_ids:
                cursor.execute("SELECT COUNT(*) FROM shapes WHERE shape_id = ?", (sid,))
                count = cursor.fetchone()[0]
                if count < 2:
                    continue
                centroid = get_shape_centroid(cursor, sid)
                if centroid:
                    shape_info.append((sid, count, centroid))

            if not shape_info:
                continue

            # Cluster shapes by centroid proximity
            clusters = []  # Each cluster: list of (shape_id, point_count, centroid)
            for si in shape_info:
                placed = False
                for cluster in clusters:
                    # Compare against centroid of first shape in cluster
                    if approx_dist_km(si[2], cluster[0][2]) < CLUSTER_DISTANCE_KM:
                        cluster.append(si)
                        placed = True
                        break
                if not placed:
                    clusters.append([si])

            if len(clusters) > 1:
                split_count += 1

            # For each cluster, pick the longest shape
            for variant_idx, cluster in enumerate(clusters):
                # Pick shape with most points
                cluster.sort(key=lambda x: x[1], reverse=True)
                best_shape_id = cluster[0][0]

                # Get all points for this shape
                cursor.execute("""
                    SELECT shape_pt_lat, shape_pt_lon
                    FROM shapes
                    WHERE shape_id = ?
                    ORDER BY shape_pt_sequence ASC
                """, (best_shape_id,))
                raw_points = [(row[0], row[1]) for row in cursor.fetchall()]

                if len(raw_points) < 2:
                    continue

                # Simplify using Douglas-Peucker
                simplified = douglas_peucker(raw_points, SIMPLIFY_TOLERANCE)

                # Store as JSON array of [lat, lon] pairs
                points_json = json.dumps([[round(p[0], 6), round(p[1], 6)] for p in simplified])

                cursor.execute(
                    "INSERT OR REPLACE INTO route_shapes VALUES (?, ?, ?, ?, ?)",
                    (route_name, operator_noc, direction_id, variant_idx, points_json)
                )

        routes_done += 1
        if routes_done % 20 == 0:
            print(f"  Processed {routes_done}/{len(route_shapes_map)} routes...")

    if split_count:
        print(f"  Split {split_count} routes into geographic clusters (same operator, different areas)")

    conn.commit()

    # Step 6: Stats
    cursor.execute("SELECT COUNT(*) FROM route_shapes")
    total_route_shapes = cursor.fetchone()[0]

    cursor.execute("SELECT route_name, operator_noc, direction_id, variant, LENGTH(points_json) FROM route_shapes ORDER BY LENGTH(points_json) DESC LIMIT 5")
    biggest = cursor.fetchall()

    cursor.execute("SELECT SUM(LENGTH(points_json)) FROM route_shapes")
    total_json_bytes = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(DISTINCT operator_noc) FROM route_shapes")
    total_operators = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM route_shapes WHERE variant > 0")
    variant_count = cursor.fetchone()[0]

    print(f"\nDone!")
    print(f"  Shapes table: {inserted} points")
    print(f"  Route shapes: {total_route_shapes} polylines across {total_operators} operators ({total_json_bytes / 1024:.0f} KB total JSON)")
    if variant_count:
        print(f"  Geographic variants: {variant_count} extra polylines from split routes")
    print(f"  Biggest polylines:")
    for name, op, dir_id, var, size in biggest:
        print(f"    Route {name} ({op}, dir {dir_id}, var {var}): {size / 1024:.1f} KB")

    # Verify with a sample
    cursor.execute("SELECT route_name, operator_noc, direction_id, variant, points_json FROM route_shapes LIMIT 1")
    sample = cursor.fetchone()
    if sample:
        pts = json.loads(sample[4])
        print(f"\n  Sample: Route {sample[0]} ({sample[1]}, dir {sample[2]}, var {sample[3]}) has {len(pts)} simplified points")

    conn.close()
    print("\nAll done. Restart the Flask server to use the new shape data.")


if __name__ == "__main__":
    main()
