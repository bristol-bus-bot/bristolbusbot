"""Tell apart routes that share a number in different towns.

First runs Bristol, Bath and Weston-super-Mare under one operator code and
reuses route numbers across them: its 4 is one route in Bath and another in
Bristol. Grouping readings by operator and number alone adds those together.

Every scheduled trip in ``expected_trips`` names its timetable route
(``route_id``). For each route we find the council areas it serves: from the
stops of its recent readings, or failing that from where its trips start. Routes
under one number that share any area are the same service (a route crossing a
boundary, or a new timetable edition with a new ID). Routes that never meet are
different services, and each gets its main area as a suffix: "4 Bath",
"4 Bristol". Numbers used in only one place keep their plain label, so most
routes and their history are unchanged. Raw readings are never rewritten; the
label is applied as they are read.
"""
import sqlite3
from collections import Counter, defaultdict

from audit_geo import geo_for, load_geo_index

AREA_LABELS = {
    "Bristol": "Bristol",
    "Bath and North East Somerset": "Bath",
    "North Somerset": "N Somerset",
    "South Gloucestershire": "S Glos",
}
# Recent days of readings used to see which areas a route serves.
READING_DAYS = 14
# An area counts as served once it has this share of a route's stops.
SERVED_SHARE = 0.05


def area_label(area):
    """Short public name for a council area, or None if it isn't one."""
    return AREA_LABELS.get(area)


class RouteSplit:
    """Route labels with the area added where a number is shared."""

    def __init__(self, shared=(), trip_areas=None, geo_index=None):
        self.shared = set(shared)
        self.trip_areas = dict(trip_areas or {})
        self.geo_index = geo_index

    def route(self, operator, route, trip_id=None, stop_code=None):
        """The label for one reading or trip; unchanged unless shared."""
        if route is None or (operator, route) not in self.shared:
            return route
        area = self.trip_areas.get(trip_id)
        if area is None and stop_code and self.geo_index:
            # A reading whose trip isn't in the schedule still sits at a stop,
            # and shared routes never meet, so its stop decides.
            area = self._stop_area(stop_code)
        return f"{route} {area}" if area else route

    def _stop_area(self, stop_code):
        place = geo_for(self.geo_index, stop_code)
        return area_label(place["area"]) if place else None


def _components(profiles):
    """Group route IDs whose served areas overlap; returns lists of IDs."""
    served = {
        rid: {area for area, n in counts.items() if n >= SERVED_SHARE * sum(counts.values())}
        for rid, counts in profiles.items() if counts
    }
    parent = {rid: rid for rid in served}

    def find(rid):
        while parent[rid] != rid:
            parent[rid] = parent[parent[rid]]
            rid = parent[rid]
        return rid

    ids = sorted(served)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if served[a] & served[b]:
                parent[find(a)] = find(b)
    groups = defaultdict(list)
    for rid in ids:
        groups[find(rid)].append(rid)
    return list(groups.values())


def _reading_areas(conn, geo_index, numbers):
    """{(operator, route, route_id): Counter(area)} from recent readings."""
    out = defaultdict(Counter)
    if not numbers:
        return out
    try:
        days = [row[0] for row in conn.execute(
            "SELECT DISTINCT service_date FROM expected_trips "
            "ORDER BY service_date DESC LIMIT ?", (READING_DAYS,))]
        for operator in sorted({op for op, _ in numbers}):
            routes = sorted(route for op, route in numbers if op == operator)
            route_ph = ",".join("?" for _ in routes)
            for day in days:
                for route, route_id, stop_code, count in conn.execute(
                        f"""SELECT o.route, e.route_id, o.stop_code, COUNT(*)
                              FROM timepoint_observations o
                              JOIN expected_trips e
                                ON e.service_date = o.service_date AND e.trip_id = o.trip_id
                             WHERE o.service_date = ? AND o.operator = ?
                               AND o.route IN ({route_ph}) AND e.route_id IS NOT NULL
                             GROUP BY o.route, e.route_id, o.stop_code""",
                        (day, operator, *routes)):
                    place = geo_for(geo_index, stop_code)
                    area = area_label(place["area"]) if place else None
                    if area:
                        out[(operator, route, route_id)][area] += count
    except sqlite3.OperationalError:
        return defaultdict(Counter)
    return out


def build(conn, geo_index=None):
    """Work out which numbers are shared, from the retained schedule."""
    try:
        groups = conn.execute(
            """SELECT operator, route, route_id, first_stop_code, COUNT(*)
                 FROM expected_trips
                WHERE route IS NOT NULL AND route != '' AND route_id IS NOT NULL
                GROUP BY operator, route, route_id, first_stop_code""").fetchall()
    except sqlite3.OperationalError:
        return RouteSplit()
    if not groups:
        return RouteSplit()
    if geo_index is None:
        try:
            geo_index = load_geo_index()
        except RuntimeError:
            return RouteSplit()
    split = RouteSplit(geo_index=geo_index)

    starts = defaultdict(lambda: defaultdict(Counter))
    for operator, route, route_id, stop_code, count in groups:
        area = split._stop_area(stop_code)
        starts[(operator, route)][route_id][area] += count if area else 0

    # Cheap first pass: only numbers whose routes start in separate places
    # need their readings checked.
    candidates = {key for key, by_route in starts.items() if len(_components(by_route)) > 1}
    readings = _reading_areas(conn, geo_index, candidates)

    shared, route_area = set(), {}
    for operator, route in candidates:
        profiles = {
            rid: readings.get((operator, route, rid)) or counts
            for rid, counts in starts[(operator, route)].items()
        }
        components = _components(profiles)
        if len(components) < 2:
            continue
        shared.add((operator, route))
        for ids in components:
            total = sum((profiles[rid] for rid in ids), Counter())
            for rid in ids:
                route_area[(operator, route, rid)] = total.most_common(1)[0][0]

    # One pass over the schedule: the table is keyed by date, so a query per
    # number would scan it once each.
    trip_areas = {}
    if shared:
        route_ph = ",".join("?" for _ in {route for _, route in shared})
        for operator, route, trip_id, route_id in conn.execute(
                f"""SELECT DISTINCT operator, route, trip_id, route_id FROM expected_trips
                     WHERE route IN ({route_ph}) AND route_id IS NOT NULL""",
                sorted({route for _, route in shared})):
            area = route_area.get((operator, route, route_id))
            if area:
                trip_areas[trip_id] = area
    split.shared = shared
    split.trip_areas = trip_areas
    return split


def column_or_null(conn, table, column):
    """``column`` for a SELECT, or NULL where an older or test table lacks it."""
    names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return column if column in names else "NULL"


_cache = {}


def for_connection(conn, geo_index=None):
    """``build`` once per connection, again only if new trips were scheduled."""
    try:
        marker = conn.execute("SELECT MAX(rowid) FROM expected_trips").fetchone()[0]
    except sqlite3.OperationalError:
        marker = None
    cached = _cache.get(id(conn))
    if cached and cached[0] is conn and cached[1] == marker:
        return cached[2]
    split = build(conn, geo_index)
    _cache.clear()
    _cache[id(conn)] = (conn, marker, split)
    return split
