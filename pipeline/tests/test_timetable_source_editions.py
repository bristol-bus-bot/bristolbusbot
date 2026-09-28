"""Source-edition lineage rules (September 2026 forward-coverage failure).

Fixtures mirror real First Bristol journeys traced in
TIMETABLE-DIAGNOSIS-20260928.md: D1x (CSBA, 27 Sep -> 4 Oct renumbering),
D1x VJ739/VJ1259 duplicate copies, and route 70's bank-holiday Monday journey.
"""
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timetable_calendar_evidence import Evidence, successor_witness
import timetable_calendar_evidence as calendar
import timetable_duplicate_evidence as duplicate
from timetable_source_index import SourceIndex, SourceJourney, represented_editions
from timetable_quality_gates import duplicate_active_schedules

DEC1 = date(2026, 12, 1)
SCOPE = ('PH0000132:01', 'D1x')
SEP27, OCT4 = date(2026, 9, 27), date(2026, 10, 4)
CALLS = (('A', '07:10:00', '07:10:00'), ('B', '07:40:00', '07:40:00'))
OTHER = (('A', '08:10:00', '08:10:00'), ('B', '08:40:00', '08:40:00'))


def profile(*days: str) -> bytes:
    regular = ''.join(f'<{day}/>' for day in days)
    return (f'<OperatingProfile><RegularDayType><DaysOfWeek>{regular}</DaysOfWeek>'
            '</RegularDayType><BankHolidayOperation><DaysOfNonOperation><ChristmasDay/>'
            '<BoxingDay/><ChristmasEve/><NewYearsEve/></DaysOfNonOperation>'
            '</BankHolidayOperation></OperatingProfile>').encode()


MON_WED = profile('Monday', 'Tuesday', 'Wednesday')
THURSDAY = profile('Thursday')
FRIDAY = profile('Friday')
MONDAY = profile('Monday')
SATURDAY = profile('Saturday')


def journey(code, start, prof=MON_WED, calls=CALLS, scope=SCOPE, line='D1x', direction=1):
    return SourceJourney(line, direction, code, calls,
                         Evidence(scope, start, None, prof, 'archive', 'file-' + code, 'member.xml'))


def index_of(*journeys):
    index = SourceIndex()
    for item in journeys:
        index.add(item)
    return index


def key(code, calls=CALLS, line='D1x', direction=1):
    return (line, direction, code, calls)


# --- Successor-edition witness (calendar repair) ---------------------------

def test_renumbered_successor_edition_proves_ordinary_day():  # F1
    index = index_of(journey('VJ1278', SEP27), journey('VJ810', OCT4, THURSDAY),
                     journey('VJ841', OCT4, MON_WED), journey('VJ872', OCT4, FRIDAY))
    found = successor_witness(DEC1, key('VJ1278'), index, {})
    assert found is not None
    predecessors, successor = found
    assert [p.start for p in predecessors] == [SEP27] and successor.code == 'VJ841'


def test_two_positive_successors_or_unknown_profiles_prove_nothing():  # F4
    index = index_of(journey('VJ1278', SEP27), journey('VJ841', OCT4), journey('VJ842', OCT4))
    assert successor_witness(DEC1, key('VJ1278'), index, {}) is None
    index = index_of(journey('VJ1278', SEP27), journey('VJ841', OCT4),
                     journey('VJ9', OCT4, b'<OperatingProfile><Unsupported/></OperatingProfile>'))
    assert successor_witness(DEC1, key('VJ1278'), index, {}) is None


def test_edition_gap_or_newer_edition_already_in_gtfs_blocks_successor():  # F6, F7
    gap = index_of(journey('VJ1278', date(2026, 9, 13)), journey('VJX', SEP27, calls=OTHER),
                   journey('VJ841', OCT4))
    assert successor_witness(DEC1, key('VJ1278'), gap, {}) is None
    index = index_of(journey('VJ1278', SEP27), journey('VJ841', OCT4))
    assert successor_witness(DEC1, key('VJ1278'), index, {SCOPE: {OCT4}}) is None


def test_holidays_and_orphans_are_never_restored():  # F8, F2
    index = index_of(journey('VJ1278', SEP27), journey('VJ841', OCT4))
    assert successor_witness(date(2026, 12, 24), key('VJ1278'), index, {}) is None
    assert successor_witness(DEC1, key('VJ739'), index, {}) is None


def test_multi_scope_lineage_is_ambiguous():  # F13
    index = index_of(journey('VJ1278', SEP27), journey('VJ1278', SEP27, scope=('OTHER', 'D1x')),
                     journey('VJ841', OCT4))
    assert successor_witness(DEC1, key('VJ1278'), index, {}) is None


def test_represented_ignores_codes_shared_with_the_predecessor():  # F17
    shared = journey('VJ1', SEP27)
    index = index_of(shared, journey('VJ1', OCT4), *[journey(f'N{i}', OCT4, calls=OTHER) for i in range(3)])
    assert OCT4 not in represented_editions(index, [key('VJ1')]).get(SCOPE, set())
    index = index_of(journey('VJ1', SEP27), journey('VJ2', OCT4, calls=OTHER))
    assert OCT4 in represented_editions(index, [key('VJ2', OTHER)])[SCOPE]


def calendar_db(path, trips):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE agency(agency_id TEXT,agency_noc TEXT);
    CREATE TABLE routes(route_id TEXT,agency_id TEXT,route_short_name TEXT);
    CREATE TABLE trips(trip_id TEXT,route_id TEXT,service_id TEXT,direction_id INT,vehicle_journey_code TEXT);
    CREATE TABLE calendar(service_id TEXT,monday INT,tuesday INT,wednesday INT,thursday INT,
        friday INT,saturday INT,sunday INT,start_date TEXT,end_date TEXT);
    CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INT);
    CREATE TABLE stop_times(trip_id TEXT,stop_id TEXT,stop_sequence INT,arrival_time TEXT,departure_time TEXT);
    INSERT INTO agency VALUES('a','FBRI');
    INSERT INTO routes VALUES('r','a','D1x');
    INSERT INTO calendar VALUES('s',1,1,1,1,0,0,0,'20260928','20270628');
    INSERT INTO calendar_dates VALUES('s','20261201',2),('s','20261224',2);
    ''')
    for trip, code, calls in trips:
        db.execute("INSERT INTO trips VALUES(?,'r','s',1,?)", (trip, code))
        db.executemany('INSERT INTO stop_times VALUES(?,?,?,?,?)',
                       [(trip, stop, n, arr, dep) for n, (stop, arr, dep) in enumerate(calls)])
    db.commit()
    return db


def test_calendar_repair_restores_lineage_trip_but_not_its_orphan_copy(tmp_path):  # F1+F2
    index = index_of(journey('VJ1278', SEP27), journey('VJ810', OCT4, THURSDAY),
                     journey('VJ841', OCT4, MON_WED))
    db = calendar_db(tmp_path / 'c.db', [('lineage', 'VJ1278', CALLS), ('orphan', 'VJ739', CALLS)])
    result = calendar.reconcile_database(tmp_path / 'c.db', tmp_path, index)
    assert result == {'trips_corrected': 1, 'exclusions_corrected': 1,
                      'successor_exclusions_corrected': 1}
    assert db.execute("SELECT service_id FROM trips WHERE trip_id='orphan'").fetchone()[0] == 's'
    proof = json.loads(db.execute('SELECT evidence_json FROM calendar_source_corrections').fetchone()[0])
    assert [item['role'] for item in proof] == ['predecessor', 'successor']
    assert proof[-1]['journey_code'] == 'VJ841'
    clone = db.execute("SELECT service_id FROM trips WHERE trip_id='lineage'").fetchone()[0]
    assert db.execute('SELECT date FROM calendar_dates WHERE service_id=?', (clone,)).fetchall() == [('20261224',)]


def test_two_lineage_claimants_for_one_successor_prove_nothing(tmp_path):  # F3
    index = index_of(journey('VJ1278', SEP27), journey('VJ1279', SEP27), journey('VJ841', OCT4))
    calendar_db(tmp_path / 'c.db', [('a', 'VJ1278', CALLS), ('b', 'VJ1279', CALLS)])
    assert calendar.reconcile_database(tmp_path / 'c.db', tmp_path, index)['trips_corrected'] == 0


# --- Superseded-edition non-operation and orphan duplicates ----------------

BRISTOL = ('PB0002032:470', '70')
AUG30, SEP13 = date(2026, 8, 30), date(2026, 9, 13)
MONDAY_DAY = date(2026, 11, 23)


def test_bank_holiday_week_monday_journey_is_retired_by_its_replacement():  # F9
    index = index_of(
        journey('VJ4924', AUG30, MONDAY, scope=BRISTOL, line='70', direction=0),
        journey('VJ1669', SEP13, SATURDAY, scope=BRISTOL, line='70', direction=0),
        journey('VJ1743', SEP13, MON_WED, calls=OTHER, scope=BRISTOL, line='70', direction=0))
    stale = ('70', 0, 'VJ4924', CALLS)
    proof = duplicate.superseded_nonoperation(MONDAY_DAY, stale, index, {BRISTOL: {SEP13}})
    assert [item.start for item in proof] == [AUG30, SEP13]
    # Not when the replacement edition is absent from GTFS, or before it starts.
    assert not duplicate.superseded_nonoperation(MONDAY_DAY, stale, index, {})
    assert not duplicate.superseded_nonoperation(date(2026, 9, 7), stale, index, {BRISTOL: {SEP13}})


def test_absent_or_positive_replacement_is_not_nonoperation():  # F10
    index = index_of(journey('VJ4924', AUG30, MONDAY, scope=BRISTOL, line='70', direction=0),
                     journey('VJ1', SEP13, MONDAY, calls=OTHER, scope=BRISTOL, line='70', direction=0))
    assert not duplicate.superseded_nonoperation(
        MONDAY_DAY, ('70', 0, 'VJ4924', CALLS), index, {BRISTOL: {SEP13}})
    index.add(journey('VJ2', SEP13, MONDAY, scope=BRISTOL, line='70', direction=0))
    assert not duplicate.superseded_nonoperation(
        MONDAY_DAY, ('70', 0, 'VJ4924', CALLS), index, {BRISTOL: {SEP13}})


def duplicate_db(path, trips, weekdays=(1, 1, 1, 0, 0, 0, 0)):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE agency(agency_id TEXT,agency_noc TEXT);
    CREATE TABLE routes(route_id TEXT,agency_id TEXT,route_short_name TEXT);
    CREATE TABLE trips(trip_id TEXT,route_id TEXT,service_id TEXT,direction_id INT,vehicle_journey_code TEXT);
    CREATE TABLE calendar(service_id TEXT,monday INT,tuesday INT,wednesday INT,thursday INT,
        friday INT,saturday INT,sunday INT,start_date TEXT,end_date TEXT);
    CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INT);
    CREATE TABLE stop_times(trip_id TEXT,stop_id TEXT,stop_sequence INT,arrival_time TEXT,departure_time TEXT,
        pickup_type INT,drop_off_type INT);
    INSERT INTO agency VALUES('a','FBRI');
    INSERT INTO routes VALUES('r','a','D1x');
    ''')
    for trip, service, code, calls in trips:
        db.execute('INSERT OR IGNORE INTO calendar VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (service, *weekdays, '20261123', '20261129'))
        db.execute("INSERT INTO trips VALUES(?,'r',?,1,?)", (trip, service, code))
        db.executemany('INSERT INTO stop_times VALUES(?,?,?,?,?,0,0)',
                       [(trip, stop, n, arr, dep) for n, (stop, arr, dep) in enumerate(calls)])
    db.commit()
    return db


def test_orphan_copy_is_retired_only_where_its_twin_is_proven(tmp_path):  # F2
    index = index_of(journey('VJ1259', SEP27, MON_WED))
    db = duplicate_db(tmp_path / 'd.db', [('orphan', 'old', 'VJ739', CALLS),
                                          ('lineage', 'new', 'VJ1259', CALLS)])
    result = duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)
    # Mon 23 - Wed 25 Nov: both copies active; the sourced twin is proven each day.
    assert result == {'trips_corrected': 1, 'dates_excluded': 3}
    rows = db.execute('SELECT trip_id,date,evidence_json FROM duplicate_source_corrections ORDER BY date').fetchall()
    assert [r[1] for r in rows] == ['20261123', '20261124', '20261125']
    assert all(json.loads(r[2])['basis'] == 'orphan_duplicate' and r[0] == 'orphan' for r in rows)
    # The sourced twin keeps its own calendar and every stop call.
    assert db.execute("SELECT service_id FROM trips WHERE trip_id='lineage'").fetchone()[0] == 'new'
    assert db.execute('SELECT count(*) FROM stop_times').fetchone()[0] == 4


def test_two_sourced_journeys_are_not_treated_as_orphans(tmp_path):  # F12/F14 guard
    index = index_of(journey('VJ1259', SEP27), journey('VJ739', SEP27))
    duplicate_db(tmp_path / 'd.db', [('a', 'old', 'VJ739', CALLS), ('b', 'new', 'VJ1259', CALLS)])
    result = duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)
    assert result == {'trips_corrected': 0, 'dates_excluded': 0}


def test_orphans_without_a_sourced_twin_are_left_alone(tmp_path):
    index = index_of(journey('VJ1259', SEP27, calls=OTHER))
    duplicate_db(tmp_path / 'd.db', [('a', 'old', 'VJ739', CALLS), ('b', 'new', 'VJ740', CALLS)])
    assert duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)['trips_corrected'] == 0


# --- Quality gate -----------------------------------------------------------

def test_cross_service_duplicates_fail_the_quality_gate(tmp_path):  # F15
    duplicate_db(tmp_path / 'q.db', [('a', 'old', 'VJ739', CALLS), ('b', 'new', 'VJ1259', CALLS)])
    result = duplicate_active_schedules(tmp_path / 'q.db', date(2026, 11, 23), days=7)
    assert not result['passed'] and result['worst']['duplicates'] == 1
    duplicate_db(tmp_path / 'ok.db', [('a', 'old', 'VJ739', CALLS), ('b', 'new', 'VJ1259', OTHER)])
    assert duplicate_active_schedules(tmp_path / 'ok.db', date(2026, 11, 23), days=7)['passed']


def test_orphan_is_retired_on_each_day_by_whichever_twin_runs_that_day(tmp_path):
    """D1x: the orphan runs Mon-Thu; its twins are a Mon-Wed and a Thursday journey."""
    index = index_of(journey('VJ1259', SEP27, MON_WED), journey('VJ1290', SEP27, THURSDAY))
    db = duplicate_db(tmp_path / 'd.db', [('orphan', 'old', 'VJ739', CALLS),
                                          ('monwed', 'mw', 'VJ1259', CALLS),
                                          ('thursday', 'th', 'VJ1290', CALLS)], weekdays=(1, 1, 1, 1, 0, 0, 0))
    db.execute("UPDATE calendar SET monday=1,tuesday=1,wednesday=1,thursday=0 WHERE service_id='mw'")
    db.execute("UPDATE calendar SET monday=0,tuesday=0,wednesday=0,thursday=1 WHERE service_id='th'")
    db.commit()
    result = duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)
    assert result == {'trips_corrected': 1, 'dates_excluded': 4}
    replacements = {row[0]: json.loads(row[1])['replacement_trip_id'] for row in db.execute(
        'SELECT date,evidence_json FROM duplicate_source_corrections')}
    assert replacements['20261126'] == 'thursday' and replacements['20261123'] == 'monwed'


def test_older_edition_copy_is_retired_even_when_profiles_are_unsupported(tmp_path):
    """Route 42 Sundays: identical 30 Aug and 13 Sep copies both active in GTFS."""
    school = b'<OperatingProfile><RegularDayType><DaysOfWeek><Sunday/></DaysOfWeek></RegularDayType><ServicedOrganisationDayType/></OperatingProfile>'
    scope = ('PB0002032:42', 'D1x')
    index = index_of(journey('VJ5', AUG30, school, scope=scope), journey('VJ4425', SEP13, school, scope=scope))
    db = duplicate_db(tmp_path / 'd.db', [('newer', 'n', 'VJ4425', CALLS), ('older', 'o', 'VJ5', CALLS)])
    result = duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)
    assert result == {'trips_corrected': 1, 'dates_excluded': 3}
    rows = db.execute('SELECT DISTINCT trip_id, json_extract(evidence_json, "$.basis") FROM duplicate_source_corrections').fetchall()
    assert rows == [('older', 'superseded_duplicate')]


def test_newer_copy_is_never_retired_in_favour_of_an_older_one(tmp_path):
    index = index_of(journey('VJ5', AUG30), journey('VJ4425', SEP13))
    db = duplicate_db(tmp_path / 'd.db', [('a_newer', 'n', 'VJ4425', CALLS), ('b_older', 'o', 'VJ5', CALLS)])
    duplicate.reconcile_database(tmp_path / 'd.db', tmp_path, index)
    assert {r[0] for r in db.execute('SELECT trip_id FROM duplicate_source_corrections')} <= {'b_older'}
