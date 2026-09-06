"""Retain the source and ambiguity of scheduled-trip denominators.

Identical schedules are investigation clues, not permission to delete trips:
two vehicles can be scheduled together. Keep both identities and withhold the
derived denominator until its collision has been resolved at source.
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
    if collisions:
        reasons.append('unresolved_identical_schedules')
    if not rows:
        reasons.append('no_scheduled_trips')
    digest = hashlib.sha256(json.dumps(sorted(fingerprints, key=lambda r: str(r[0][0])),
                                      separators=(',', ':')).encode()).hexdigest()
    return dict(snapshot_sha256=digest, collision_groups=len(collisions),
                # Bounded diagnostic examples; counts always cover the full day.
                collision_examples=sorted(collisions)[:20],
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
    conn.execute('INSERT OR IGNORE INTO expected_snapshot_versions VALUES (?,?,?,?,?,?)',
                 (day, evidence['snapshot_sha256'], now, timetable_sha256,
                  evidence['trip_count'], json.dumps(evidence, sort_keys=True)))
    conn.execute('INSERT OR REPLACE INTO expected_snapshot_quality VALUES (?,?,?,?,?,?,?)',
                 (day, now, timetable_sha256, evidence['snapshot_sha256'],
                  evidence['trip_count'], evidence['collision_groups'],
                  json.dumps(sorted(set(reasons)))))


def denominator_reasons(conn, day: str) -> list[str]:
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
