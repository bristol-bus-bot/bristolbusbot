"""Build-time checks that catch too much service, not only too little.

The existing acceptance gates compare a candidate with the live timetable and
reject shortfalls. They cannot see duplicated journeys or phantom service from a
superseded source edition, because extra trips always look like "more
coverage". These checks run on the disposable candidate after reconciliation.

``duplicate_active_schedules``
    FBRI trips active on the same day that share route, direction and the
    complete ordered stop/arrival/departure sequence under different
    service IDs. The canonical volume metric only collapses copies inside one
    service, so these count twice everywhere else.

``source_schedule_agreement``
    FBRI distinct active schedules compared with First's own TXC: journeys of
    the edition in force for each scope that positively operate that day
    (strict evaluator), restricted to lines the candidate carries. Unknown or
    unsupported profiles widen the upper bound; they never count as operating.

Only FBRI can fail: no authoritative source exists for other operators.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import sqlite3
from pathlib import Path

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DUPLICATE_RATE_LIMIT = 0.005
SOURCE_UPPER_RATIO = 1.10
SOURCE_LOWER_RATIO = 0.85
DEFAULT_DAYS = 180
SOURCE_DAYS = 42


def _parse(text: str) -> date:
    return datetime.strptime(text, '%Y%m%d').date()


def _fbri_activity(connection: sqlite3.Connection):
    trips = {}
    for trip, service, route, direction in connection.execute('''
            SELECT t.trip_id, t.service_id, r.route_short_name, COALESCE(t.direction_id, 0)
            FROM trips t JOIN routes r USING(route_id) JOIN agency a USING(agency_id)
            WHERE a.agency_noc='FBRI' '''):
        trips[trip] = (service, route, int(direction))
    signature = {}
    current, digest = None, None
    for trip, stop, arrival, departure in connection.execute(
            'SELECT trip_id, stop_id, arrival_time, departure_time FROM stop_times '
            'ORDER BY trip_id, stop_sequence'):
        if trip != current:
            if current in trips:
                signature[current] = digest.hexdigest()
            current, digest = trip, hashlib.sha256()
        digest.update(f'{stop}|{arrival}|{departure};'.encode())
    if current in trips:
        signature[current] = digest.hexdigest()
    calendars = {row[0]: row for row in connection.execute(
        'SELECT service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, '
        'start_date, end_date FROM calendar')}
    exceptions = defaultdict(dict)
    for service, day, kind in connection.execute(
            'SELECT service_id, date, exception_type FROM calendar_dates'):
        exceptions[service][day] = int(kind)
    return trips, signature, calendars, exceptions


def _active(service, day: date, calendars, exceptions) -> bool:
    text = day.strftime('%Y%m%d')
    kind = exceptions.get(service, {}).get(text)
    if kind == 1:
        return True
    if kind == 2:
        return False
    row = calendars.get(service)
    return bool(row and row[8] <= text <= row[9] and int(row[1 + day.weekday()]) == 1)


def duplicate_active_schedules(path: Path, start: date, days: int = DEFAULT_DAYS) -> dict:
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
        trips, signature, calendars, exceptions = _fbri_activity(connection)
    by_service = defaultdict(list)
    for trip, (service, route, direction) in trips.items():
        if trip in signature:
            by_service[service].append((route, direction, signature[trip]))
    worst = {'date': None, 'duplicates': 0, 'active': 0, 'rate': 0.0}
    failing = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        seen = defaultdict(set)
        active = 0
        for service, items in by_service.items():
            if not _active(service, day, calendars, exceptions):
                continue
            for item in items:
                active += 1
                seen[item].add(service)
        duplicates = sum(len(services) - 1 for services in seen.values() if len(services) > 1)
        rate = duplicates / active if active else 0.0
        if rate > worst['rate']:
            worst = {'date': day.isoformat(), 'duplicates': duplicates, 'active': active,
                     'rate': round(rate, 6)}
        if rate > DUPLICATE_RATE_LIMIT:
            failing.append(day.isoformat())
    return {'limit': DUPLICATE_RATE_LIMIT, 'days': days, 'worst': worst,
            'failing_dates': len(failing), 'first_failing_date': failing[0] if failing else None,
            'passed': not failing}


def source_schedule_agreement(path: Path, index, start: date,
                              days: int = SOURCE_DAYS) -> dict:
    from timetable_operating_days import operating_day
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
        trips, signature, calendars, exceptions = _fbri_activity(connection)
    lines = {route for _, route, _ in trips.values()}
    by_service = defaultdict(set)
    for trip, (service, route, direction) in trips.items():
        if trip in signature:
            by_service[service].add((route, direction, signature[trip]))
    journeys_by_edition = defaultdict(list)
    for journey in index.journeys:
        if journey.line in lines:
            journeys_by_edition[(journey.scope, journey.start)].append(journey)
    results, failures = [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        candidate = set()
        for service, items in by_service.items():
            if _active(service, day, calendars, exceptions):
                candidate |= items
        positive, unknown = set(), set()
        for scope in index.editions:
            if scope[1] not in lines:
                continue
            in_force = index.in_force(scope, day)
            if in_force is None:
                continue
            for journey in journeys_by_edition.get((scope, in_force), ()):
                state = operating_day(journey.evidence.profile, day)
                key = (journey.line, journey.direction, journey.calls)
                if state is True:
                    positive.add(key)
                elif state is None:
                    unknown.add(key)
        unknown -= positive
        upper = (len(positive) + len(unknown)) * SOURCE_UPPER_RATIO
        lower = len(positive) * SOURCE_LOWER_RATIO
        record = {'date': day.isoformat(), 'candidate': len(candidate),
                  'source_positive': len(positive), 'source_unknown': len(unknown),
                  'passed': lower <= len(candidate) <= upper}
        results.append(record)
        if not record['passed']:
            failures.append(record)
    return {'upper_ratio': SOURCE_UPPER_RATIO, 'lower_ratio': SOURCE_LOWER_RATIO,
            'days': days, 'failures': failures[:20], 'failing_dates': len(failures),
            'passed': not failures,
            'sample': results[:7]}


def check_candidate_quality(path: Path, index, start: date) -> dict:
    duplicates = duplicate_active_schedules(path, start)
    agreement = source_schedule_agreement(path, index, start) if index is not None else None
    passed = duplicates['passed'] and (agreement is None or agreement['passed'])
    return {'duplicate_active_schedules': duplicates,
            'source_schedule_agreement': agreement, 'passed': passed}
