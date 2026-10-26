import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
PERIOD = [{'StartTime': '2026-10-20T08:00:00Z', 'EndTime': '2026-10-30T18:00:00Z'}]


def _module():
    path = Path(__file__).resolve().parent.parent / 'scripts' / 'notice_gaps.py'
    spec = importlib.util.spec_from_file_location('notice_gaps', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_counts_each_gap_by_publisher(app):
    live = sqlite3.connect(app.config['BBB'].live_db)
    live.row_factory = sqlite3.Row
    rows = [
        ('weca-stops', 'WestofEngland', 'roadworks', 'Road closed', {'validity_periods': PERIOD, 'stops': [{'stop_ref': 'S1'}]}, '2026-10-24'),
        ('weca-text', 'WestofEngland', 'roadworks', 'Service 43 diverts missing Baden Road stop', {'validity_periods': PERIOD}, '2026-10-23'),
        ('first-legacy', 'FirstBristol', None, 'Diversion', {'stops': [{'stop_ref': 'S2'}]}, '2026-10-22'),
        ('weca-vague', 'WestofEngland', None, 'Works', {'validity_periods': []}, '2026-10-21'),
        ('weca-old', 'WestofEngland', None, 'Old', {'validity_periods': PERIOD}, '2026-08-01'),
    ]
    for number, who, reason, summary, affected, day in rows:
        live.execute('''INSERT INTO situations (situation_number, participant, progress, reason, summary,
            description, versioned_at, affected_json, updated_at) VALUES (?,?,'open',?,?,'',?,?,?)''',
            (number, who, reason, summary, f'{day}T09:00:00+00:00', json.dumps(affected), f'{day}T09:00:00+00:00'))
    report = _module().measure(live, days=28, now=NOW)
    assert report['situations'] == 4
    assert report['publishers']['WestofEngland'] == {
        'total': 3, 'roadworks': 2, 'with_stops': 1, 'stop_in_text_only': 1, 'uncertain_dates': 1}
    assert report['publishers']['FirstBristol'] == {'total': 1, 'with_stops': 1, 'skipped_no_dates': 1}
    assert [s['id'] for s in report['stop_in_text_only']] == ['weca-text']
    live.close()
