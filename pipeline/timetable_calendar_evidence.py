"""Reconcile GTFS removal exceptions only with exact First TXC evidence.

This runs on the disposable build, never on the live database. Unsupported
profiles, conflicting sources and changed schedules retain the GTFS exclusion.
It does not relax delivery acceptance or manufacture historical observations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache

from frequency_changes import england_wales_bank_holidays
import txc_parser as txc

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
HOLIDAYS = {
    'ChristmasDay', 'BoxingDay', 'GoodFriday', 'NewYearsDay',
    'LateSummerBankHolidayNotScotland', 'MayDay', 'EasterMonday', 'SpringBank',
    'ChristmasDayHoliday', 'BoxingDayHoliday', 'NewYearsDayHoliday',
    'ChristmasEve', 'NewYearsEve',
}


@lru_cache(maxsize=1024)
def ordinary_operating_day(profile_xml: bytes, day: date) -> bool:
    """Prove a simple regular day; decline all holidays and complex profiles."""
    try:
        # The parser's provenance hash appends organisation definitions even
        # when this journey's simple Sunday profile never refers to them.
        # Keep those definitions in evidence, but inspect the actual profile.
        document = ET.fromstring(b'<Evidence>' + profile_xml + b'</Evidence>')
        if not len(document) or document[0].tag != 'OperatingProfile' or any(
                item.tag != 'ServicedOrganisation' for item in list(document)[1:]):
            return False
        root = document[0]
        if root.tag != 'OperatingProfile' or any(
                e.tag not in {'RegularDayType', 'BankHolidayOperation'} for e in root):
            return False
        if len(root.findall('RegularDayType')) != 1 or len(root.findall('BankHolidayOperation')) > 1:
            return False
        regular = root.find('RegularDayType')
        if regular is None or len(regular) != 1 or regular[0].tag != 'DaysOfWeek':
            return False
        allowed = {s.title() for s in WEEKDAYS} | {
            'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'Weekend'}
        if any(e.tag not in allowed or len(e) for e in regular[0]):
            return False
        profile = txc.OperatingProfile(root, {})
        if day.weekday() not in {d.day for d in profile.regular_days}:
            return False
        blocked = set(england_wales_bank_holidays(day.year)) | {
            date(day.year, 1, 1), date(day.year, 12, 24),
            date(day.year, 12, 25), date(day.year, 12, 26), date(day.year, 12, 31)}
        bank = root.find('BankHolidayOperation')
        if bank is not None:
            if len(bank) > 1 or any(e.tag != 'DaysOfNonOperation' for e in bank):
                return False
            for rules in bank:
                for rule in rules:
                    if rule.tag == 'OtherPublicHoliday':
                        if any(e.tag not in {'Description', 'Date'} for e in rule):
                            return False
                        blocked.add(date.fromisoformat(rule.findtext('Date', '')))
                    elif rule.tag not in HOLIDAYS or len(rule):
                        return False
        return day not in blocked
    except (ET.ParseError, ValueError, KeyError):
        return False


def _time(delta) -> str:
    seconds = int(delta.total_seconds())
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


@dataclass(frozen=True)
class Evidence:
    scope: tuple[str, str]
    start: date
    end: date | None
    profile: bytes
    archive_sha256: str
    file_sha256: str
    member: str

    def record(self) -> dict:
        return dict(service_code=self.scope[0], line=self.scope[1],
                    start=self.start.isoformat(), end=str(self.end),
                    archive_sha256=self.archive_sha256,
                    file_sha256=self.file_sha256, member=self.member,
                    profile_sha256=hashlib.sha256(self.profile).hexdigest())


def source_evidence(directory: Path, wanted: set) -> tuple[dict, dict]:
    """Keep exact schedule witnesses and all edition starts for their services."""
    evidence = defaultdict(list)
    editions = defaultdict(set)
    wanted_lines = {key[0] for key in wanted}
    wanted_heads = {key[:3] for key in wanted}
    for path in sorted(directory.glob('*.zip')):
        with path.open('rb') as handle:
            archive_sha = hashlib.file_digest(handle, 'sha256').hexdigest()
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.lower().endswith('.xml'):
                    continue
                if info.file_size > 128 * 1024 * 1024:
                    raise RuntimeError('TXC member exceeds calendar evidence size limit')
                raw = archive.read(info)
                if b'FBRI' not in raw:
                    continue
                # Avoid parsing unrelated national First files.
                if not any(('<LineName>'+line+'</LineName>').encode() in raw
                           for line in wanted_lines):
                    continue
                doc = txc.TransXChange(io.BytesIO(raw))
                nocs = {op.get('id'): op.findtext('NationalOperatorCode')
                        for op in getattr(doc, 'operators', [])}
                file_sha = hashlib.sha256(raw).hexdigest()
                for service in doc.services.values():
                    if nocs.get(service.operator) != 'FBRI':
                        continue
                    period = service.operating_period
                    if not period.start:
                        raise RuntimeError('First source has no operating period start')
                    for line in service.lines:
                        if line.line_name not in wanted_lines:
                            continue
                        scope = (service.service_code, line.line_name)
                        editions[scope].add(period.start)
                        for journey in doc.get_journeys(service.service_code, line.id):
                            if journey.operator and nocs.get(journey.operator) != 'FBRI':
                                continue
                            head = (line.line_name,
                                    1 if journey.journey_pattern.is_inbound() else 0,
                                    journey.code)
                            if head not in wanted_heads:
                                continue
                            profile = journey.operating_profile or service.operating_profile
                            cells = list(journey.get_times())
                            if len(cells) < 2:
                                continue
                            schedule = tuple((c.stopusage.stop.atco_code,
                                              _time(c.arrival_time), _time(c.departure_time))
                                             for c in cells)
                            key = (*head, schedule)
                            if key in wanted:
                                evidence[key].append(Evidence(
                                    scope, period.start, period.end,
                                    profile.hash if profile else b'', archive_sha,
                                    file_sha, info.filename))
    return evidence, editions


def witnesses_for(day: date, candidates: list[Evidence], editions: dict) -> list[Evidence]:
    """All applicable latest-edition witnesses must positively agree."""
    active = []
    for item in candidates:
        starts = [d for d in editions[item.scope] if d <= day]
        if not starts or item.start != max(starts):
            continue
        if item.end is not None and day > item.end:
            continue
        active.append(item)
    if not active or any(not ordinary_operating_day(item.profile, day) for item in active):
        return []
    return active


def successor_witness(day: date, key, index, represented):
    """Carry an exact older-edition journey into a newer edition not yet in GTFS.

    First renumbers journey codes in every edition, and BODS GTFS can lag behind
    a newly published edition. The exact-code rule then finds the GTFS journey
    only in the edition immediately before the one in force on ``day``. That is
    accepted only when: the journey's lineage has one scope; the edition in force
    is the very next edition and is not carried by the GTFS; and in that edition
    exactly one journey with identical complete calls positively operates on
    this ordinary day while every other identical-schedule journey there
    explicitly does not. Returns ``(predecessor_witnesses, successor_journey)``
    or None. Claim uniqueness is enforced by the caller.
    """
    from timetable_operating_days import operating_day
    from timetable_source_index import single_scope_lineage
    found = single_scope_lineage(index, key)
    if found is None:
        return None
    scope, newest, witnesses = found
    in_force = index.in_force(scope, day)
    if in_force is None or in_force <= newest or in_force in represented.get(scope, ()):
        return None
    if index.predecessor(scope, in_force) != newest:
        return None  # an intermediate edition exists: no single proven step
    same = index.same_schedule(key[0], key[1], key[3], scope, in_force)
    positive = [journey for journey in same if ordinary_operating_day(journey.evidence.profile, day)]
    if len(positive) != 1:
        return None
    if any(operating_day(journey.evidence.profile, day) is not False
           for journey in same if journey is not positive[0]):
        return None
    predecessors = [witness for witness in witnesses if witness.start == newest]
    return predecessors, positive[0]


def reconcile_database(database: Path, directory: Path, index=None) -> dict:
    """Clone only proven trips' calendars; retain every unrelated exception."""
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT t.trip_id, t.service_id, r.route_short_name, t.direction_id,
                   t.vehicle_journey_code, c.*, x.date AS excluded_date
            FROM trips t JOIN routes r USING(route_id)
            JOIN agency a USING(agency_id) JOIN calendar c USING(service_id)
            JOIN calendar_dates x ON x.service_id=t.service_id
            WHERE a.agency_noc='FBRI' AND x.exception_type=2
              AND x.date BETWEEN c.start_date AND c.end_date
        """).fetchall()
        targets = {}
        dates = defaultdict(set)
        for row in rows:
            day = datetime.strptime(row['excluded_date'], '%Y%m%d').date()
            if row[WEEKDAYS[day.weekday()]] != 1:
                continue
            targets[row['trip_id']] = row
            dates[row['trip_id']].add(day)
        if not targets:
            return {'trips_corrected': 0, 'exclusions_corrected': 0}
        schedules = defaultdict(list)
        for trip, stop, arrival, departure in connection.execute(
                'SELECT trip_id,stop_id,arrival_time,departure_time FROM stop_times '
                'ORDER BY trip_id,stop_sequence'):
            if trip in targets:
                schedules[trip].append((stop, arrival, departure))
        keys = {trip: (r['route_short_name'], r['direction_id'], r['vehicle_journey_code'],
                       tuple(schedules[trip])) for trip, r in targets.items()}
        if index is None:
            evidence, editions = source_evidence(directory, set(keys.values()))
        else:
            evidence = {key: index.exact(key) for key in set(keys.values())}
            editions = index.editions
        corrections = {}
        for trip, key in keys.items():
            proven = {day: [item.record() for item in witnesses_for(day, evidence.get(key, []), editions)]
                      for day in dates[trip]}
            proven = {day: items for day, items in proven.items() if items}
            if proven:
                corrections[trip] = proven
        successors = 0
        if index is not None:
            successors = _successor_corrections(connection, index, keys, dates, corrections)
        connection.execute("""CREATE TABLE IF NOT EXISTS calendar_source_corrections (
            trip_id TEXT NOT NULL, date TEXT NOT NULL, original_service_id TEXT NOT NULL,
            corrected_service_id TEXT NOT NULL, evidence_json TEXT NOT NULL,
            PRIMARY KEY(trip_id,date))""")
        clones = set()
        for trip, proven in corrections.items():
            original = targets[trip]['service_id']
            date_strings = sorted(day.strftime('%Y%m%d') for day in proven)
            digest = hashlib.sha256(json.dumps([original, date_strings]).encode()).hexdigest()
            clone = 'TXC_CAL_' + digest
            if clone not in clones:
                if connection.execute('SELECT 1 FROM calendar WHERE service_id=?', (clone,)).fetchone():
                    raise RuntimeError('calendar evidence clone already exists')
                connection.execute('INSERT INTO calendar SELECT ?,monday,tuesday,wednesday,'
                                   'thursday,friday,saturday,sunday,start_date,end_date '
                                   'FROM calendar WHERE service_id=?', (clone, original))
                connection.execute('INSERT INTO calendar_dates SELECT ?,date,exception_type '
                                   'FROM calendar_dates WHERE service_id=?', (clone, original))
                connection.executemany('DELETE FROM calendar_dates WHERE service_id=? '
                                       'AND date=? AND exception_type=2',
                                       [(clone, day) for day in date_strings])
                clones.add(clone)
            connection.execute('UPDATE trips SET service_id=? WHERE trip_id=?', (clone, trip))
            for day, items in proven.items():
                connection.execute('INSERT INTO calendar_source_corrections VALUES (?,?,?,?,?)',
                                   (trip, day.strftime('%Y%m%d'), original, clone,
                                    json.dumps(items, sort_keys=True)))
        result = {'trips_corrected': len(corrections),
                  'exclusions_corrected': sum(map(len, corrections.values()))}
        if index is not None:
            result['successor_exclusions_corrected'] = successors
        return result


def _successor_corrections(connection, index, keys, dates, corrections) -> int:
    """Add unique successor-edition proofs; ambiguous claims prove nothing."""
    from timetable_source_index import represented_editions
    all_keys = []
    current, calls, head = None, [], {}
    for trip, route, direction, code in connection.execute('''
            SELECT t.trip_id, r.route_short_name, t.direction_id, t.vehicle_journey_code
            FROM trips t JOIN routes r USING(route_id) JOIN agency a USING(agency_id)
            WHERE a.agency_noc='FBRI' '''):
        head[trip] = (route, direction, code)
    for trip, stop, arrival, departure in connection.execute(
            'SELECT trip_id,stop_id,arrival_time,departure_time FROM stop_times '
            'ORDER BY trip_id,stop_sequence'):
        if trip != current:
            if current in head:
                all_keys.append((*head[current], tuple(calls)))
            current, calls = trip, []
        calls.append((stop, arrival, departure))
    if current in head:
        all_keys.append((*head[current], tuple(calls)))
    represented = represented_editions(index, all_keys)
    claims = defaultdict(list)
    for trip, key in keys.items():
        for day in dates[trip]:
            if day in corrections.get(trip, {}):
                continue
            found = successor_witness(day, key, index, represented)
            if found:
                predecessors, successor = found
                claims[(successor.scope, successor.start, successor.code, day)].append(
                    (trip, predecessors, successor))
    # A successor journey already claimed by an exact correction is also taken.
    exact_claims = set()
    for trip, proven in corrections.items():
        for day in proven:
            for journey in index.exact_journeys(keys[trip]):
                exact_claims.add((journey.scope, journey.start, journey.code, day))
    added = 0
    for claim, holders in claims.items():
        if len(holders) != 1 or claim in exact_claims:
            continue
        trip, predecessors, successor = holders[0]
        day = claim[3]
        records = [dict(item.record(), role='predecessor') for item in predecessors]
        records.append(dict(successor.evidence.record(), role='successor',
                            journey_code=successor.code, basis='successor_edition'))
        corrections.setdefault(trip, {})[day] = records
        added += 1
    return added
