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

from timetable_calendar_evidence import source_evidence, witnesses_for, WEEKDAYS


def active_days(calendar, exceptions):
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
        profiles = {item.profile for item in older + candidates}
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
        current, calls = None, []

        def finish(trip, rows):
            if trip not in trips or len(rows)<2:
                return
            t=trips[trip]
            full=tuple(rows)
            schedules[trip]=tuple(row[:3] for row in full)
            digest=hashlib.sha256(json.dumps(full,separators=(',',':')).encode()).hexdigest()
            groups[(t['route_id'],t['direction_id'],digest)].append(trip)

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
        days={service:active_days(c,exceptions[service]) for service,c in calendars.items()}
        corrections=defaultdict(dict)
        for ids in groups:
            for old in ids:
                for new in ids:
                    if old==new:continue
                    common=days.get(trips[old]['service_id'],set()) & days.get(trips[new]['service_id'],set())
                    for day in sorted(common):
                        proof=replacement_proof(day,keys[old],keys[new],evidence,editions)
                        if proof:
                            corrections[old][day.strftime('%Y%m%d')]=dict(
                                replacement_trip_id=new,witnesses=[w.record() for w in proof])
        conn.execute('''CREATE TABLE IF NOT EXISTS duplicate_source_corrections (
            trip_id TEXT NOT NULL,date TEXT NOT NULL,original_service_id TEXT NOT NULL,
            corrected_service_id TEXT NOT NULL,evidence_json TEXT NOT NULL,
            PRIMARY KEY(trip_id,date))''')
        for trip,excluded in corrections.items():
            original=trips[trip]['service_id']
            clone='BBBDUP_'+hashlib.sha256((trip+json.dumps(sorted(excluded))).encode()).hexdigest()[:24]
            calendar=calendars[original]
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
