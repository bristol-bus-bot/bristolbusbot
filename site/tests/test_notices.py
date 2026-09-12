import json
import sqlite3
from datetime import datetime, timezone

import pytest
from app.services.notices import for_context, _window_status

NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)
PERIODS = [
    {'StartTime': '2026-09-15T08:30:00Z', 'EndTime': '2026-09-15T14:00:00Z'},
    {'StartTime': '2026-09-16T08:30:00Z', 'EndTime': '2026-09-16T14:00:00Z'},
]


@pytest.mark.parametrize('at,expected', [
    ('2026-09-15T08:29:00+00:00', 'upcoming'),
    ('2026-09-15T08:30:00+00:00', 'current'),
    ('2026-09-15T14:00:00+00:00', 'upcoming'),
    ('2026-09-16T10:00:00+00:00', 'current'),
    ('2026-09-16T14:00:00+00:00', 'expired'),
])
def test_disjoint_windows(at, expected):
    assert _window_status(PERIODS, '', datetime.fromisoformat(at))[0] == expected


def test_source_conflict_and_missing_dates():
    assert _window_status(PERIODS, 'Closed from 20:00 to 06:00', NOW)[0] == 'uncertain'
    assert _window_status(PERIODS, 'Closed from 09:30 to 15:00', NOW)[0] == 'current'
    assert _window_status([], '', NOW)[0] == 'uncertain'
    assert _window_status([{'StartTime': 'nonsense'}], '', NOW)[0] == 'uncertain'


def test_context_lifecycle_and_freshness(app):
    live = sqlite3.connect(app.config['BBB'].live_db)
    live.row_factory = sqlite3.Row
    tt = sqlite3.connect(app.config['BBB'].timetable_db)
    tt.row_factory = sqlite3.Row
    affected = {'validity_periods': PERIODS, 'stops': [{'stop_ref': 'S2'}],
                'lines': [{'operator': 'FBRI', 'line': '43'}]}
    live.execute("INSERT INTO poller_status VALUES ('siri_sx', ?, ?, 0, 'ok')", (NOW.isoformat(), NOW.isoformat()))
    live.execute('''INSERT INTO situations (situation_number,participant,progress,summary,description,
        versioned_at,affected_json,updated_at,link) VALUES ('notice','WestofEngland','open','Stop closed','Advice',?,?,?,?)''',
        (NOW.isoformat(), json.dumps(affected), NOW.isoformat(), 'javascript:alert(1)'))
    def query(**kw):
        return for_context(live, tt, now=NOW, **kw)
    item = query(stop='0100B')['notices'][0]
    assert item['status'] == 'current' and item['url'].startswith('https://')
    assert query(stop='0100A')['notices'] == []
    assert len(query(operator='FBRI', line='43')['notices']) == 1
    assert query(operator='OTHER', line='43')['notices'] == []
    live.execute("UPDATE situations SET progress='closed'")
    assert query(stop='0100B')['notices'] == []
    live.execute("UPDATE situations SET progress='open', closed_at=?", (NOW.isoformat(),))
    assert query(stop='0100B')['notices'] == []
    live.execute("UPDATE situations SET closed_at=NULL, affected_json='{}'")
    assert query(stop='0100B')['notices'] == []
    live.execute("UPDATE poller_status SET last_success_at='2026-09-15T09:00:00Z' WHERE name='siri_sx'")
    assert query(stop='0100B')['available'] is False
    live.close(); tt.close()


def test_api_requires_unambiguous_context(client):
    assert client.get('/api/notices').status_code == 400
    assert client.get('/api/notices?stop=a&operator=FBRI&line=43').status_code == 400
    response = client.get('/api/notices?stop=0100B')
    assert response.status_code == 200
    assert response.get_json()['available'] is False
