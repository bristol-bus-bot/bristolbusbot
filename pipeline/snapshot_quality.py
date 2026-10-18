"""Retain the source and ambiguity of scheduled-trip denominators.

Identical schedules are investigation clues, not permission to delete trips:
two vehicles can be scheduled together. Keep both identities and withhold the
derived denominator until its collision has been resolved at source.

Withholding is per route, not per day. A collision on one route says nothing
about the other routes' denominators, so only the affected (operator, route)
pairs lose their coverage figures; everything else publishes. Days recorded
before route-level evidence existed stay withheld for the whole day until
``backfill_route_withholds`` derives their routes from the stored snapshot.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import sqlite3


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS expected_snapshot_quality (
        service_date TEXT PRIMARY KEY,
        captured_at TEXT NOT NULL,
        timetable_sha256 TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        trip_count INTEGER NOT NULL,
        collision_groups INTEGER NOT NULL,
        reasons_json TEXT NOT NULL
    )""")
    # One row per service day whose collisions were attributed to routes.
    # Its presence is what lets a day-level collision reason be narrowed.
    conn.execute("""CREATE TABLE IF NOT EXISTS expected_snapshot_route_scope (
        service_date TEXT PRIMARY KEY,
        method TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS expected_snapshot_route_withholds (
        service_date TEXT NOT NULL,
        operator TEXT NOT NULL,
        route TEXT NOT NULL,
        reason TEXT NOT NULL,
        trips INTEGER NOT NULL,
        PRIMARY KEY (service_date, operator, route, reason)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS expected_snapshot_versions (
        service_date TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        timetable_sha256 TEXT NOT NULL,
        trip_count INTEGER NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY (service_date, snapshot_sha256)
    )""")


def inspect_snapshot(tt: sqlite3.Connection, rows: list[tuple]) -> dict:
    """Fingerprint every ordered stop call, preserving real variants."""
    columns = {r[1] for r in tt.execute('PRAGMA table_info(stop_times)')}
    fields = ['stop_id', 'departure_time', 'arrival_time', 'pickup_type', 'drop_off_type']
    sql_fields = ','.join(f if f in columns else 'NULL' for f in fields)
    groups = defaultdict(list)
    fingerprints = []
    reasons = []
    for row in rows:
        trip_id, operator, route, direction = row[:4]
        calls = tt.execute(f'SELECT {sql_fields} FROM stop_times '
                           'WHERE trip_id=? ORDER BY stop_sequence', (trip_id,)).fetchall()
        calls = [tuple(call) for call in calls]
        if not calls or not row[5] or direction is None:
            reasons.append('incomplete_schedule_identity')
        # Registered route ID prevents collapsing different towns' route 6.
        key = (operator, row[5], direction, tuple(calls))
        groups[key].append(str(trip_id))
        fingerprints.append((tuple(row), calls))
    collisions = [sorted(ids) for ids in groups.values() if len(ids) > 1]
    route_of = {str(row[0]): (row[1], row[2]) for row in rows}
    if collisions:
        reasons.append('unresolved_identical_schedules')
    route_trips = defaultdict(int)
    for ids in collisions:
        for trip_id in ids:
            route_trips[route_of[trip_id]] += 1
    if not rows:
        reasons.append('no_scheduled_trips')
    digest = hashlib.sha256(json.dumps(sorted(fingerprints, key=lambda r: str(r[0][0])),
                                      separators=(',', ':')).encode()).hexdigest()
    return dict(snapshot_sha256=digest, collision_groups=len(collisions),
                # Bounded diagnostic examples; counts always cover the full day.
                collision_examples=sorted(collisions)[:20],
                collision_routes=[[op, route, count] for (op, route), count
                                  in sorted(route_trips.items(), key=lambda i: (str(i[0][0]), str(i[0][1])))],
                reasons=sorted(set(reasons)), trip_count=len(rows))


def record_quality(conn, day: str, timetable_sha256: str, evidence: dict) -> None:
    """Write in the same transaction as expected_trips; retain prior versions."""
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    previous = conn.execute('SELECT snapshot_sha256, reasons_json '
                            'FROM expected_snapshot_quality WHERE service_date=?',
                            (day,)).fetchone()
    reasons = list(evidence['reasons'])
    if previous:
        old_reasons = json.loads(previous[1])
        if previous[0] != evidence['snapshot_sha256'] or 'snapshot_changed' in old_reasons:
            reasons.append('snapshot_changed')
    route_withholds = [(op, route, 'unresolved_identical_schedules', count)
                       for op, route, count in evidence.get('collision_routes', [])]
    if 'collision_routes' in evidence:
        _write_route_withholds(conn, day, 'snapshot_calls', route_withholds, now)
    conn.execute('INSERT OR IGNORE INTO expected_snapshot_versions VALUES (?,?,?,?,?,?)',
                 (day, evidence['snapshot_sha256'], now, timetable_sha256,
                  evidence['trip_count'], json.dumps(evidence, sort_keys=True)))
    conn.execute('INSERT OR REPLACE INTO expected_snapshot_quality VALUES (?,?,?,?,?,?,?)',
                 (day, now, timetable_sha256, evidence['snapshot_sha256'],
                  evidence['trip_count'], evidence['collision_groups'],
                  json.dumps(sorted(set(reasons)))))


ROUTE_LEVEL_REASONS = {'unresolved_identical_schedules'}


def _write_route_withholds(conn, day, method, rows, now) -> None:
    conn.execute('DELETE FROM expected_snapshot_route_withholds WHERE service_date=?', (day,))
    conn.executemany('INSERT INTO expected_snapshot_route_withholds VALUES (?,?,?,?,?)',
                     [(day, str(op), '' if route is None else str(route), reason, int(count))
                      for op, route, reason, count in rows])
    conn.execute('INSERT OR REPLACE INTO expected_snapshot_route_scope VALUES (?,?,?)',
                 (day, method, now))


def _has_table(conn, name) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (name,)).fetchone())


def route_scope_recorded(conn, day: str) -> bool:
    return _has_table(conn, 'expected_snapshot_route_scope') and bool(conn.execute(
        'SELECT 1 FROM expected_snapshot_route_scope WHERE service_date=?', (day,)).fetchone())


def withheld_routes(conn, day: str) -> dict[tuple[str, str], list[str]]:
    """(operator, route) -> reasons its coverage denominator is withheld."""
    if not route_scope_recorded(conn, day):
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    for op, route, reason in conn.execute(
            'SELECT operator, route, reason FROM expected_snapshot_route_withholds '
            'WHERE service_date=? ORDER BY operator, route, reason', (day,)):
        result.setdefault((op, route), []).append(reason)
    return result


def backfill_route_withholds(conn, day: str) -> dict:
    """Attribute an old day's collisions to routes from its stored snapshot.

    expected_trips keeps each trip's route, direction, first and last
    departure and first stop, but not every call. Trips identical in every
    call are necessarily identical in these fields, so grouping by them finds
    every true collision and possibly a few extra look-alikes: it can only
    withhold more routes than the full check would, never fewer. Returns the
    routes withheld; refuses when the day has no stored snapshot.
    """
    init_schema(conn)
    rows = conn.execute(
        'SELECT operator, route, route_id, direction, first_departure, last_departure, '
        'first_stop_id FROM expected_trips WHERE service_date=?', (day,)).fetchall()
    if not rows:
        raise RuntimeError(f'{day}: no expected_trips snapshot to attribute')
    groups = defaultdict(list)
    for index, (op, route, route_id, direction, first, last, stop) in enumerate(rows):
        if not route_id or direction is None or not first or not last or not stop:
            key = ('incomplete', index)  # cannot be compared, so never groups
        else:
            key = (op, route_id, direction, first, last, stop)
        groups[key].append((op, route))
    counts = defaultdict(int)
    for members in groups.values():
        if len(members) > 1:
            for op, route in members:
                counts[(op, route)] += 1
    now = datetime.now(timezone.utc).isoformat()
    withholds = [(op, route, 'unresolved_identical_schedules', n)
                 for (op, route), n in sorted(counts.items(), key=lambda i: (str(i[0][0]), str(i[0][1])))]
    _write_route_withholds(conn, day, 'expected_trips_signature', withholds, now)
    return {'routes': [[op, route, n] for op, route, _, n in withholds]}


def denominator_reasons(conn, day: str) -> list[str]:
    """Reasons the whole day's coverage denominator is withheld.

    Route-level reasons are left out once the day's collisions have been
    attributed to routes; ``withheld_routes`` then names the routes."""
    reasons = _day_reasons(conn, day)
    if route_scope_recorded(conn, day):
        reasons = [reason for reason in reasons if reason not in ROUTE_LEVEL_REASONS]
    return reasons


def _day_reasons(conn, day: str) -> list[str]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='expected_snapshot_quality'").fetchone():
        return ['snapshot_provenance_unavailable']
    row = conn.execute('SELECT reasons_json, timetable_sha256, snapshot_sha256 '
                       'FROM expected_snapshot_quality WHERE service_date=?', (day,)).fetchone()
    if row is None:
        return ['snapshot_provenance_unavailable']
    if not row[1] or not row[2]:
        return ['snapshot_provenance_unavailable']
    return json.loads(row[0])


def main(argv=None) -> int:
    """Attended use: attribute an old day's twin collisions to routes.

    python3 snapshot_quality.py --attribute-routes 20260929 [20260930 ...]
    Prints the routes that will be withheld; nothing else changes until the
    rollup for that day is re-run.
    """
    import os
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != '--attribute-routes' or len(args) < 2:
        print(main.__doc__)
        return 2
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.getenv('BBB_AUDIT_DB', os.path.join(here, 'audit.db'))
    conn = sqlite3.connect(path)
    try:
        for day in args[1:]:
            with conn:
                before = denominator_reasons(conn, day)
                result = backfill_route_withholds(conn, day)
                after = denominator_reasons(conn, day)
            routes = ', '.join(f'{op} {route or "(unknown)"} ({n} trips)'
                               for op, route, n in result['routes']) or 'none'
            print(f'{day}: day reasons {before or "none"} -> {after or "none"}; '
                  f'routes withheld: {routes}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
