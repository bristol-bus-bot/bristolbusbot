from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import timetable_calendar_evidence as calendar

PROFILE = b'''<OperatingProfile><RegularDayType><DaysOfWeek>
<MondayToFriday/></DaysOfWeek></RegularDayType><BankHolidayOperation>
<DaysOfNonOperation><ChristmasDay/><BoxingDayHoliday/><NewYearsDayHoliday/>
<OtherPublicHoliday><Description>One off</Description><Date>2022-06-03</Date>
</OtherPublicHoliday></DaysOfNonOperation></BankHolidayOperation></OperatingProfile>'''
DAY = date(2026, 12, 1)
PROOF = calendar.Evidence(('service', 'X4'), date(2026, 9, 13), None,
                          PROFILE, 'archive-hash', 'xml-hash', 'source.xml')


def test_first_regular_weekday_source_contradicts_december_exclusion():
    assert calendar.ordinary_operating_day(PROFILE, DAY)


@pytest.mark.parametrize('day', [date(2026, 12, 25), date(2026, 12, 28),
                               date(2027, 1, 1), date(2026, 12, 24),
                               date(2026, 12, 5), date(2022, 6, 3)])
def test_holidays_and_nonoperating_weekdays_stay_excluded(day):
    assert not calendar.ordinary_operating_day(PROFILE, day)


@pytest.mark.parametrize('extra', [b'<SpecialDaysOperation/>',
                                  b'<ServicedOrganisationDayType/>',
                                  b'<PeriodicDayType/>', b'<UnknownRule/>'])
def test_complex_or_unknown_profile_is_not_proof(extra):
    profile = PROFILE.replace(b'</OperatingProfile>', extra + b'</OperatingProfile>')
    assert not calendar.ordinary_operating_day(profile, DAY)


def test_unknown_holiday_and_explicit_date_are_not_overridden():
    assert not calendar.ordinary_operating_day(PROFILE.replace(b'ChristmasDay/', b'UnknownHoliday/'), DAY)
    assert not calendar.ordinary_operating_day(PROFILE.replace(b'2022-06-03', b'2026-12-01'), DAY)


def test_expired_superseded_and_conflicting_sources_are_not_proof():
    editions = {PROOF.scope: {PROOF.start}}
    assert calendar.witnesses_for(DAY, [PROOF], editions) == [PROOF]
    assert not calendar.witnesses_for(DAY, [replace(PROOF, end=date(2026, 11, 30))], editions)
    assert not calendar.witnesses_for(DAY, [PROOF], {PROOF.scope: {PROOF.start, date(2026, 11, 1)}})
    opposite = replace(PROOF, profile=PROFILE.replace(b'MondayToFriday', b'Saturday'))
    assert not calendar.witnesses_for(DAY, [PROOF, opposite], editions)


def database(path):
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
    INSERT INTO routes VALUES('r','a','X4');
    INSERT INTO calendar VALUES('shared',1,1,1,1,1,0,0,'20260913','20270613');
    INSERT INTO calendar_dates VALUES('shared','20261201',2),('shared','20261225',2),('shared','20261206',1);
    INSERT INTO trips VALUES('proved','r','shared',1,'VJ1'),('unproved','r','shared',1,'VJ2');
    INSERT INTO stop_times VALUES('proved','A',0,'10:17:00','10:17:00'),('proved','B',1,'11:34:00','11:34:00');
    ''')
    db.commit()
    return db


def test_exact_proof_changes_only_proven_trip_and_preserves_other_exceptions(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.db'
    db = database(path)
    key = ('X4', 1, 'VJ1', (('A', '10:17:00', '10:17:00'), ('B', '11:34:00', '11:34:00')))
    monkeypatch.setattr(calendar, 'source_evidence', lambda *_: ({key: [PROOF]}, {PROOF.scope: {PROOF.start}}))
    assert calendar.reconcile_database(path, tmp_path) == {'trips_corrected': 1, 'exclusions_corrected': 1}
    new = db.execute("SELECT service_id FROM trips WHERE trip_id='proved'").fetchone()[0]
    assert new != 'shared'
    assert db.execute("SELECT service_id FROM trips WHERE trip_id='unproved'").fetchone()[0] == 'shared'
    assert db.execute('SELECT date,exception_type FROM calendar_dates WHERE service_id=? ORDER BY date', (new,)).fetchall() == [('20261206', 1), ('20261225', 2)]
    assert db.execute("SELECT count(*) FROM calendar_dates WHERE service_id='shared'").fetchone()[0] == 3
    assert 'xml-hash' in db.execute('SELECT evidence_json FROM calendar_source_corrections').fetchone()[0]
    assert calendar.reconcile_database(path, tmp_path)['trips_corrected'] == 0


@pytest.mark.parametrize('position,value', [(0, 'U2'), (1, 0), (2, 'VJwrong'),
                                          (3, (('A','10:18:00','10:18:00'),))])
def test_route_direction_code_or_schedule_mismatch_cannot_remove_exclusion(tmp_path, monkeypatch, position, value):
    path = tmp_path / 'candidate.db'
    db = database(path)
    key = ['X4', 1, 'VJ1', (('A', '10:17:00', '10:17:00'), ('B', '11:34:00', '11:34:00'))]
    key[position] = value
    monkeypatch.setattr(calendar, 'source_evidence', lambda *_: ({tuple(key): [PROOF]}, {PROOF.scope: {PROOF.start}}))
    assert calendar.reconcile_database(path, tmp_path)['trips_corrected'] == 0
    assert db.execute('SELECT DISTINCT service_id FROM trips').fetchall() == [('shared',)]
