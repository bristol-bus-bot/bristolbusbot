"""Retire superseded identical journeys only with exact operator evidence.

No trip or stop-time row is deleted. A private calendar clone excludes the old
identity only on dates where the latest source edition proves its replacement.
Unproven collisions remain visible to snapshot quality checks.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
from functools import lru_cache

from timetable_calendar_evidence import source_evidence, witnesses_for, WEEKDAYS


def active_days(calendar, exceptions):
    if calendar is None:
        return {datetime.strptime(day, '%Y%m%d').date()
                for day, kind in exceptions if kind == 1}
    start = datetime.strptime(calendar['start_date'], '%Y%m%d').date()
    end = datetime.strptime(calendar['end_date'], '%Y%m%d').date()
    if (end - start).days > 730:
        return set()  # Unsupported long calendars are never silently shortened.
    days = {start + timedelta(days=n) for n in range((end-start).days+1)
            if calendar[WEEKDAYS[(start+timedelta(days=n)).weekday()]]}
    for text, kind in exceptions:
        day = datetime.strptime(text, '%Y%m%d').date()
        if kind == 1:
            days.add(day)
        elif kind == 2:
            days.discard(day)
    return days


@lru_cache(maxsize=4096)
def profile_conditions_on_day(profile, weekday):
    """Compare remaining conditions only after both regular masks allow this day.

    This does not evaluate school calendars or add service. It proves that a
    Tuesday-only mask and a Monday-Friday mask impose the same other conditions
    on Tuesday, retaining every organisation definition in the comparison.
    """
    try:
        doc = ET.fromstring(b'<Evidence>' + profile + b'</Evidence>')
        if not len(doc) or doc[0].tag != 'OperatingProfile' or any(
                item.tag != 'ServicedOrganisation' for item in list(doc)[1:]):
            return None
        root = doc[0]
        regular = root.findall('RegularDayType')
        if len(regular) != 1 or len(regular[0]) != 1 or regular[0][0].tag != 'DaysOfWeek':
            return None
        masks = {name.title(): {i} for i, name in enumerate(WEEKDAYS)}
        masks.update(MondayToFriday=set(range(5)), MondayToSaturday=set(range(6)),
                     MondayToSunday=set(range(7)), Weekend={5, 6})
        days = set()
        for item in regular[0][0]:
            if item.tag not in masks or len(item):
                return None
            days.update(masks[item.tag])
        if weekday not in days:
            return None
        root.remove(regular[0])
        return ET.canonicalize(ET.tostring(doc), strip_text=True)
    except ET.ParseError:
        return None


def replacement_proof(day, old, new, evidence, editions):
    older = [item for item in evidence.get(old, []) if item.start <= day]
    newer = witnesses_for(day, evidence.get(new, []), editions)
    def latest(items):
        return [item for item in items
                if item.start == max((d for d in editions[item.scope] if d <= day), default=None)
                and (item.end is None or day <= item.end)]
    if not older or latest(older):
        return []
    if not newer:
        candidates = latest(evidence.get(new, []))
        # This is a replacement proof, not permission to add operating dates.
        # Identical complete profiles (including school-calendar definitions)
        # impose identical conditions on both already-active GTFS journeys.
        # Retiring the superseded ID cannot remove their shared stop service.
        profiles = {profile_conditions_on_day(item.profile, day.weekday())
                    for item in older + candidates}
        if not candidates or len(profiles) != 1 or not next(iter(profiles)):
            return []
        newer = candidates
    scopes = {w.scope for w in older + newer}
    if len(scopes) != 1:
        return []
    # An absent/mismatched latest source alone is not proof: we also need the
    # positively operating, identical replacement and an earlier source ID.
    if max(w.start for w in older) >= min(w.start for w in newer):
        return []
    return older + newer


def same_source_alias_proof(day, key, evidence, editions):
    """One latest source journey can have several GTFS IDs, not several buses."""
    if not key[2]:
        return []
    active = witnesses_for(day, evidence.get(key, []), editions)
    # Require one actual latest declaration, not merely matching display text.
    if len(active) != 1:
        return []
    return active


def resolve_replacements(corrections):
    """Every receipt must name a surviving journey, including alias chains."""
    original={(trip,day):proof for trip,dates in corrections.items()
              for day,proof in dates.items()}
    for (trip,day),proof in original.items():
        target=proof['replacement_trip_id']
        seen={trip}
        witnesses=list(proof['witnesses'])
        while (target,day) in original:
            if target in seen:
                raise RuntimeError('cyclic timetable replacement evidence')
            seen.add(target)
            next_proof=original[(target,day)]
            witnesses.extend(next_proof['witnesses'])
            target=next_proof['replacement_trip_id']
        corrections[trip][day]=dict(replacement_trip_id=target,witnesses=witnesses)


def reconcile_database(database: Path, directory: Path) -> dict:
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        trips = {r['trip_id']: dict(r) for r in conn.execute('''
            SELECT t.*,r.route_short_name FROM trips t JOIN routes r USING(route_id)
            JOIN agency a USING(agency_id) WHERE a.agency_noc='FBRI'
        ''')}
        fields = {r[1] for r in conn.execute('PRAGMA table_info(stop_times)')}
        permissions = ','.join(f if f in fields else 'NULL' for f in ['pickup_type','drop_off_type'])
        groups = defaultdict(list)
        schedules = {}
        source_calls={}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='supplement_trip_sources'").fetchone():
            source_calls={trip:tuple(tuple(call) for call in json.loads(raw))
                          for trip,raw in conn.execute('SELECT trip_id,full_calls_json FROM supplement_trip_sources')}
        current, calls = None, []

        def finish(trip, rows):
            if trip not in trips or len(rows)<2:
                return
            t=trips[trip]
            full=tuple(rows)
            schedules[trip]=source_calls.get(trip,tuple(row[:3] for row in full))
            digest=hashlib.sha256(json.dumps(full,separators=(',',':')).encode()).hexdigest()
            groups[(t['route_id'],t['direction_id'],digest,source_calls.get(trip))].append(trip)

        for row in conn.execute('SELECT trip_id,stop_id,arrival_time,departure_time,'+
                                permissions+' FROM stop_times ORDER BY trip_id,stop_sequence'):
            if row[0]!=current:
                finish(current,calls)
                current,calls=row[0],[]
            calls.append(tuple(row)[1:])
        finish(current,calls)
        groups=[ids for ids in groups.values() if len(ids)>1]
        targets={trip for ids in groups for trip in ids}
        keys={trip:(trips[trip]['route_short_name'],trips[trip]['direction_id'],
                    trips[trip]['vehicle_journey_code'],schedules[trip]) for trip in targets}
        if not targets:
            return {'trips_corrected':0,'dates_excluded':0}
        evidence,editions=source_evidence(directory,set(keys.values()))
        calendars={r['service_id']:dict(r) for r in conn.execute('SELECT * FROM calendar')}
        exceptions=defaultdict(list)
        for service,day,kind in conn.execute('SELECT service_id,date,exception_type FROM calendar_dates'):
            exceptions[service].append((day,kind))
        days={service:active_days(calendars.get(service),exceptions[service])
              for service in set(calendars) | set(exceptions)}
        corrections=defaultdict(dict)
        for ids in groups:
            for old in ids:
                for new in ids:
                    if old==new:continue
                    common=days.get(trips[old]['service_id'],set()) & days.get(trips[new]['service_id'],set())
                    for day in sorted(common):
                        proof=replacement_proof(day,keys[old],keys[new],evidence,editions)
                        if (not proof and old > new and keys[old] == keys[new]
                                and new == min(t for t in ids if keys[t] == keys[new])):
                            proof=same_source_alias_proof(day,keys[old],evidence,editions)
                        if proof:
                            corrections[old][day.strftime('%Y%m%d')]=dict(
                                replacement_trip_id=new,witnesses=[w.record() for w in proof])
        resolve_replacements(corrections)
        conn.execute('''CREATE TABLE IF NOT EXISTS duplicate_source_corrections (
            trip_id TEXT NOT NULL,date TEXT NOT NULL,original_service_id TEXT NOT NULL,
            corrected_service_id TEXT NOT NULL,evidence_json TEXT NOT NULL,
            PRIMARY KEY(trip_id,date))''')
        for trip,excluded in corrections.items():
            original=trips[trip]['service_id']
            clone='BBBDUP_'+hashlib.sha256((trip+json.dumps(sorted(excluded))).encode()).hexdigest()[:24]
            calendar=calendars.get(original)
            if calendar is None:
                dates=sorted(day.strftime('%Y%m%d') for day in days[original])
                calendar=dict.fromkeys(WEEKDAYS,0)
                calendar.update(start_date=dates[0],end_date=dates[-1])
            conn.execute('INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (clone,*[calendar[k] for k in WEEKDAYS],calendar['start_date'],calendar['end_date']))
            conn.execute('INSERT INTO calendar_dates SELECT ?,date,exception_type FROM calendar_dates WHERE service_id=?',
                         (clone,original))
            for day,proof in excluded.items():
                conn.execute('DELETE FROM calendar_dates WHERE service_id=? AND date=?',(clone,day))
                conn.execute('INSERT INTO calendar_dates VALUES (?,?,2)',(clone,day))
                conn.execute('INSERT INTO duplicate_source_corrections VALUES (?,?,?,?,?)',
                             (trip,day,original,clone,json.dumps(proof,sort_keys=True)))
            conn.execute('UPDATE trips SET service_id=? WHERE trip_id=?',(clone,trip))
        return {'trips_corrected':len(corrections),
                'dates_excluded':sum(len(days) for days in corrections.values())}
