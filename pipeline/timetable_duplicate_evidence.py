"""Retire superseded identical journeys only with exact operator evidence.

No trip or stop-time row is deleted. A private calendar clone excludes the old
identity only on dates where the latest source edition proves its replacement,
or an exact source declaration explicitly rules out that operating day.
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
from timetable_operating_days import nonoperation_witnesses


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


def _stale_lineage(key, index, represented):
    """True when a newer edition of the journey's only scope is carried by GTFS."""
    from timetable_source_index import single_scope_lineage
    found = single_scope_lineage(index, key)
    if found is None:
        return False
    scope, newest, _ = found
    return any(start > newest for start in represented.get(scope, ()))


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
        corrections[trip][day]=dict(proof,replacement_trip_id=target,witnesses=witnesses)


def lineage_duplicate_proof(day, old_key, new_key, index):
    """Retire a GTFS copy superseded by an identical copy from a newer edition.

    Used only inside an identical-journey group (same route, direction and
    complete calls with permissions), on days both copies are active in GTFS.
    The surviving copy carries exactly the same stop service, so no scheduled
    service is removed, whatever the day's profile says. Two cases:

    * orphan: no available source edition declares the old copy at all
      (First withdrew that edition), while the survivor has single-scope
      lineage in an edition that has started;
    * superseded: both copies have single-scope lineage in the same scope and
      the survivor's latest started edition is later than the old copy's
      newest edition.

    Returns ``(basis, witnesses)`` or ``(None, [])``.
    """
    from timetable_source_index import single_scope_lineage
    new = single_scope_lineage(index, new_key)
    if new is None:
        return None, []
    scope, _, new_witnesses = new
    # The survivor's edition in force is its latest edition that has started.
    # First publishes editions ahead of their start date, so the newest one is
    # often still in the future; that must not block retiring an orphan.
    started = [item.start for item in new_witnesses if item.start <= day]
    if not started:
        return None, []
    new_start = max(started)
    survivor = [item for item in new_witnesses if item.start == new_start]
    if not index.exact(old_key):
        return 'orphan_duplicate', survivor
    old = single_scope_lineage(index, old_key)
    if old is None or old[0] != scope or old[1] >= new_start:
        return None, []
    return 'superseded_duplicate', [item for item in old[2] if item.start == old[1]] + survivor


def superseded_nonoperation(day, key, index, represented):
    """Explicit non-operation stated by the newer edition that replaced this one.

    Applies only when the journey's whole lineage lies in one scope and is older
    than the edition in force on ``day``, the GTFS carries that newer edition,
    and every journey there with identical complete calls explicitly does not
    operate on ``day``. Absence of the schedule, unsupported profiles and
    unknown results never count.
    """
    from timetable_operating_days import operating_day
    from timetable_source_index import single_scope_lineage
    found = single_scope_lineage(index, key)
    if found is None:
        return []
    scope, newest, witnesses = found
    in_force = index.in_force(scope, day)
    if in_force is None or in_force <= newest or in_force not in represented.get(scope, ()):
        return []
    same = index.same_schedule(key[0], key[1], key[3], scope, in_force)
    if not same or any(operating_day(journey.evidence.profile, day) is not False for journey in same):
        return []
    return [item for item in witnesses if item.start == newest] + [journey.evidence for journey in same]


def reconcile_database(database: Path, directory: Path, index=None) -> dict:
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
        represented = {}
        if index is not None:
            from timetable_source_index import represented_editions
            all_keys = {trip:(t['route_short_name'],t['direction_id'],t['vehicle_journey_code'],schedules[trip])
                        for trip,t in trips.items() if trip in schedules}
            represented = represented_editions(index, all_keys.values())
            stale = {trip for trip,key in all_keys.items()
                     if _stale_lineage(key, index, represented)}
            for trip in stale:
                keys.setdefault(trip, all_keys[trip])
            targets = targets | stale
        if not targets and index is None:
            return {'trips_corrected':0,'dates_excluded':0}
        if index is None:
            evidence,editions=source_evidence(directory,set(keys.values()))
        else:
            evidence={key:index.exact(key) for key in set(keys.values())}
            editions=index.editions
        calendars={r['service_id']:dict(r) for r in conn.execute('SELECT * FROM calendar')}
        exceptions=defaultdict(list)
        for service,day,kind in conn.execute('SELECT service_id,date,exception_type FROM calendar_dates'):
            exceptions[service].append((day,kind))
        days={service:active_days(calendars.get(service),exceptions[service])
              for service in set(calendars) | set(exceptions)}
        corrections=defaultdict(dict)
        excluded_by_source=defaultdict(dict)
        collision_days=defaultdict(set)
        for ids in groups:
            for old in ids:
                for new in ids:
                    if old != new:
                        collision_days[old].update(days.get(trips[old]['service_id'],set()) &
                                                   days.get(trips[new]['service_id'],set()))
        for trip in targets:
            for day in collision_days[trip]:
                proof=nonoperation_witnesses(day, evidence.get(keys[trip], []))
                if proof:
                    excluded_by_source[trip][day.strftime('%Y%m%d')]=dict(
                        reason='exact_source_nonoperation', witnesses=[w.record() for w in proof])
        if index is not None:
            # Journeys from an edition First has since withdrawn: BODS GTFS can
            # keep carrying them after the TXC that declared them is gone, so
            # they have no lineage. When the exact same calls appear (under a
            # renumbered code) in First's current editions and every such
            # declaration says the journey does not run that day, retire it
            # that day. This is the same strict non-operation test, applied to
            # the identical journey rather than the identical code.
            for trip,key in all_keys.items():
                if trip in stale or index.exact(key):
                    continue
                same=[journey.evidence for journey in index.schedule_journeys(key[0],key[1],key[3])]
                if not same:
                    continue
                for day in sorted(days.get(trips[trip]['service_id'], set())):
                    text=day.strftime('%Y%m%d')
                    if text in excluded_by_source[trip]:
                        continue
                    proof=nonoperation_witnesses(day, same)
                    if proof:
                        excluded_by_source[trip][text]=dict(
                            reason='withdrawn_edition_nonoperation',
                            witnesses=[w.record() for w in proof])
            # Journeys absent from the edition of their route that the GTFS
            # itself carries (proven by exact matches, see represented_editions):
            # leftovers of a withdrawn edition. Only where one unambiguous First
            # scope serves the journey's first stop in that direction, or the
            # route has only one First scope. The witnesses are that edition's
            # journeys from the same stop (or on the same route).
            by_first_stop=defaultdict(list)
            by_line_direction=defaultdict(list)
            for journey in index.journeys:
                by_first_stop[(journey.line,journey.direction,journey.calls[0][0])].append(journey)
                by_line_direction[(journey.line,journey.direction)].append(journey)
            for trip,key in all_keys.items():
                if trip in stale or index.exact(key) or index.schedule_journeys(key[0],key[1],key[3]):
                    continue
                serving=by_first_stop.get((key[0],key[1],key[3][0][0]),[])
                if not serving:
                    # A leftover can start at a stop the current edition no
                    # longer starts from; then only a route with a single First
                    # scope identifies the edition.
                    line_scopes={scope for scope in index.editions if scope[1]==key[0]}
                    if len(line_scopes)==1:
                        serving=[journey for journey in by_line_direction[(key[0],key[1])]]
                scopes={journey.scope for journey in serving}
                if len(scopes)!=1:
                    continue
                scope=next(iter(scopes))
                for day in sorted(days.get(trips[trip]['service_id'], set())):
                    text=day.strftime('%Y%m%d')
                    if text in excluded_by_source[trip]:
                        continue
                    # The newest edition the GTFS carries that has started:
                    # later editions First has published ahead are not in the
                    # GTFS yet, so they say nothing about this copy.
                    carried=[start for start in represented.get(scope, ()) if start<=day]
                    if not carried:
                        continue
                    current=[journey for journey in serving if journey.start==max(carried)]
                    if not current:
                        continue
                    excluded_by_source[trip][text]=dict(
                        reason='absent_from_current_edition',
                        witnesses=[journey.evidence.record() for journey in current[:3]])
            for trip in stale:
                for day in sorted(days.get(trips[trip]['service_id'], set())):
                    text=day.strftime('%Y%m%d')
                    if text in excluded_by_source[trip]:
                        continue
                    proof=superseded_nonoperation(day, keys[trip], index, represented)
                    if proof:
                        excluded_by_source[trip][text]=dict(
                            reason='superseded_edition_nonoperation', witnesses=[w.record() for w in proof])
        for ids in groups:
            for old in ids:
                for new in ids:
                    if old==new:continue
                    common=days.get(trips[old]['service_id'],set()) & days.get(trips[new]['service_id'],set())
                    for day in sorted(common):
                        if any(day.strftime('%Y%m%d') in excluded_by_source[t] for t in (old,new)):
                            continue
                        proof=replacement_proof(day,keys[old],keys[new],evidence,editions)
                        if (not proof and old > new and keys[old] == keys[new]
                                and new == min(t for t in ids if keys[t] == keys[new])):
                            proof=same_source_alias_proof(day,keys[old],evidence,editions)
                        basis=None
                        if (not proof and index is not None
                                and day.strftime('%Y%m%d') not in corrections[old]):
                            # The first qualifying twin in stable trip order is recorded.
                            basis,proof=lineage_duplicate_proof(day,keys[old],keys[new],index)
                        if proof:
                            corrections[old][day.strftime('%Y%m%d')]=dict(
                                replacement_trip_id=new,witnesses=[w.record() for w in proof],
                                **({'basis':basis} if basis else {}))
        resolve_replacements(corrections)
        conn.execute('''CREATE TABLE IF NOT EXISTS duplicate_source_corrections (
            trip_id TEXT NOT NULL,date TEXT NOT NULL,original_service_id TEXT NOT NULL,
            corrected_service_id TEXT NOT NULL,evidence_json TEXT NOT NULL,
            PRIMARY KEY(trip_id,date))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS calendar_nonoperation_corrections (
            trip_id TEXT NOT NULL,date TEXT NOT NULL,original_service_id TEXT NOT NULL,
            corrected_service_id TEXT NOT NULL,evidence_json TEXT NOT NULL,
            PRIMARY KEY(trip_id,date))''')
        all_corrections={trip:{**corrections.get(trip,{}), **excluded_by_source.get(trip,{})}
                         for trip in set(corrections) | set(excluded_by_source)
                         if corrections.get(trip) or excluded_by_source.get(trip)}
        # Build every row in memory and insert in bulk. A fresh build has no
        # ANALYZE statistics, so a per-date "DELETE ... WHERE service_id=? AND
        # date=?" is planned on the date index and rescans every row for that
        # date; at hundreds of thousands of excluded dates that exceeded the
        # 45-minute CI limit.
        calendar_rows, date_rows, trip_rows = [], [], []
        receipt_rows = defaultdict(list)
        for trip,excluded in all_corrections.items():
            original=trips[trip]['service_id']
            clone='BBBDUP_'+hashlib.sha256((trip+json.dumps(sorted(excluded))).encode()).hexdigest()[:24]
            calendar=calendars.get(original)
            # Exception-only services must remain exception-only. Inventing a
            # calendar start would also invent a route-edition identity after
            # normalization has already recorded the real calendar cohorts.
            if calendar is not None:
                calendar_rows.append((clone,*[calendar[k] for k in WEEKDAYS],
                                      calendar['start_date'],calendar['end_date']))
            # Copy the original exceptions, replacing every excluded date with a
            # single removal (the same result as copy, delete, then insert).
            date_rows.extend((clone,day,kind) for day,kind in exceptions[original]
                             if day not in excluded)
            for day,proof in excluded.items():
                date_rows.append((clone,day,2))
                table=('calendar_nonoperation_corrections' if 'reason' in proof
                       else 'duplicate_source_corrections')
                receipt_rows[table].append((trip,day,original,clone,json.dumps(proof,sort_keys=True)))
            trip_rows.append((clone,trip))
        conn.executemany('INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)',calendar_rows)
        conn.executemany('INSERT INTO calendar_dates VALUES (?,?,?)',date_rows)
        for table,rows in receipt_rows.items():
            conn.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?)',rows)
        conn.executemany('UPDATE trips SET service_id=? WHERE trip_id=?',trip_rows)
        return {'trips_corrected':len(all_corrections),
                'dates_excluded':sum(len(days) for days in all_corrections.values())}
