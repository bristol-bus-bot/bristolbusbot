import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snapshot_quality import inspect_snapshot, record_quality, denominator_reasons


def fixture():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE stop_times (trip_id, stop_sequence, stop_id, departure_time, arrival_time, pickup_type, drop_off_type)')
    rows = []
    for trip, end, route, block in [('old','B','R','old-block'),('new','B','R','new-block'),('short','C','R','x'),('other-town','B','OTHER','y')]:
        c.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)',
                      [(trip,0,'A','08:00:00','08:00:00',0,0),(trip,1,end,'08:30:00','08:30:00',0,0)])
        rows.append((trip,'FBRI','6',0,'08:00:00',route,'calendar',block,'code','A','code','20260906','08:30:00'))
    return c, rows


def test_collision_requires_complete_same_route_schedule_and_keeps_all_trips():
    c, rows = fixture()
    result = inspect_snapshot(c, rows)
    assert result['collision_examples'] == [['new','old']]
    assert result['trip_count'] == 4
    assert result['reasons'] == ['unresolved_identical_schedules']
    assert c.execute('SELECT count(*) FROM stop_times').fetchone()[0] == 8


def test_pickup_restriction_preserves_distinct_service():
    c, rows = fixture()
    c.execute("UPDATE stop_times SET pickup_type=1 WHERE trip_id='new' AND stop_sequence=0")
    assert inspect_snapshot(c, rows)['collision_groups'] == 0


def test_same_trip_id_retiming_is_preserved_as_changed_not_silently_certified():
    c, rows = fixture()
    rows = rows[2:]
    first = inspect_snapshot(c, rows)
    record_quality(c,'20260906','a'*64,first)
    assert denominator_reasons(c,'20260906') == []
    c.execute("UPDATE stop_times SET departure_time='08:35:00' WHERE trip_id='short' AND stop_sequence=1")
    second = inspect_snapshot(c, rows)
    assert first['snapshot_sha256'] != second['snapshot_sha256']
    record_quality(c,'20260906','b'*64,second)
    record_quality(c,'20260906','b'*64,second)
    assert denominator_reasons(c,'20260906') == ['snapshot_changed']
    assert c.execute('SELECT count(*) FROM expected_snapshot_versions').fetchone()[0] == 2


def test_missing_historical_provenance_is_unavailable_not_zero():
    c = sqlite3.connect(':memory:')
    assert denominator_reasons(c,'20260722') == ['snapshot_provenance_unavailable']
