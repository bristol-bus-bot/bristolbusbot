import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snapshot_quality import (inspect_snapshot, record_quality, denominator_reasons,
                              withheld_routes, backfill_route_withholds)


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


def test_a_collision_withholds_only_its_own_route():
    c, rows = fixture()
    result = inspect_snapshot(c, rows)
    assert result['collision_routes'] == [['FBRI', '6', 2]]
    record_quality(c, '20260929', 'a'*64, result)
    # The day as a whole publishes; route 6 is withheld on its own.
    assert denominator_reasons(c, '20260929') == []
    assert withheld_routes(c, '20260929') == {('FBRI', '6'): ['unresolved_identical_schedules']}
    # The day-level record keeps the original reason for the history.
    assert 'unresolved_identical_schedules' in c.execute(
        'SELECT reasons_json FROM expected_snapshot_quality').fetchone()[0]


def test_old_days_stay_withheld_whole_until_routes_are_attributed():
    c, rows = fixture()
    c.execute('CREATE TABLE expected_trips (service_date, operator, route, trip_id, route_id, '
              'direction, first_departure, last_departure, first_stop_id)')
    # A day recorded before route attribution existed.
    from snapshot_quality import init_schema
    init_schema(c)
    c.execute("INSERT INTO expected_snapshot_quality VALUES ('20260929','t','a','b',4,1,"
              "'[\"unresolved_identical_schedules\"]')")
    assert denominator_reasons(c, '20260929') == ['unresolved_identical_schedules']
    c.executemany('INSERT INTO expected_trips VALUES (?,?,?,?,?,?,?,?,?)', [
        ('20260929', 'SSWL', '10', 'a', 'R10', 0, '08:00:00', '09:00:00', 'S1'),
        ('20260929', 'SSWL', '10', 'b', 'R10', 0, '08:00:00', '09:00:00', 'S1'),
        ('20260929', 'SSWL', '10', 'c', 'R10', 0, '08:15:00', '09:15:00', 'S1'),
        ('20260929', 'FBRI', '72', 'd', 'R72', 0, '08:00:00', '09:00:00', 'S1'),
        ('20260929', 'FBRI', '72', 'e', 'R72', 1, '08:00:00', '09:00:00', 'S1'),
        # Incomplete identity never groups with anything.
        ('20260929', 'FBRI', '2', 'f', None, 0, '08:00:00', '09:00:00', 'S1'),
        ('20260929', 'FBRI', '2', 'g', None, 0, '08:00:00', '09:00:00', 'S1'),
    ])
    assert backfill_route_withholds(c, '20260929') == {'routes': [['SSWL', '10', 2]]}
    assert denominator_reasons(c, '20260929') == []
    assert set(withheld_routes(c, '20260929')) == {('SSWL', '10')}


def test_backfill_refuses_a_day_without_a_snapshot():
    import pytest
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE expected_trips (service_date, operator, route, trip_id, route_id, '
              'direction, first_departure, last_departure, first_stop_id)')
    with pytest.raises(RuntimeError):
        backfill_route_withholds(c, '20260929')
