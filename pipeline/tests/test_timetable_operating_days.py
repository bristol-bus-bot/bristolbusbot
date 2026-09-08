from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
import sqlite3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timetable_calendar_evidence import Evidence
from timetable_operating_days import operating_day, nonoperation_witnesses

REGULAR = b'<OperatingProfile><RegularDayType><DaysOfWeek><MondayToSaturday/></DaysOfWeek></RegularDayType>{}</OperatingProfile>'
ORG = b'<ServicedOrganisation><OrganisationCode>UOB</OrganisationCode><Name>University</Name><WorkingDays><DateRange><StartDate>2026-09-11</StartDate><EndDate>2026-12-19</EndDate></DateRange></WorkingDays></ServicedOrganisation>'


def profile(kind='Holidays', operation='DaysOfOperation'):
    rule = f'<ServicedOrganisationDayType><{operation}><{kind}><ServicedOrganisationRef>UOB</ServicedOrganisationRef></{kind}></{operation}></ServicedOrganisationDayType>'.encode()
    return REGULAR.replace(b'{}', rule) + ORG


@pytest.mark.parametrize('day,holiday', [(10, True), (11, False), (12, False), (15, False)])
def test_term_start_is_inclusive_and_holiday_journeys_do_not_run_in_term(day, holiday):
    d = date(2026, 9, day)
    assert operating_day(profile(), d) is holiday
    assert operating_day(profile('WorkingDays'), d) is not holiday
    assert operating_day(profile('WorkingDays', 'DaysOfNonOperation'), d) is holiday


def test_regular_weekdays_still_apply_with_school_rules():
    assert operating_day(profile('WorkingDays'), date(2026, 9, 13)) is False
    p = REGULAR.replace(b'{}', b'').replace(b'MondayToSaturday', b'Friday')
    assert operating_day(p, date(2026, 9, 10)) is False
    assert operating_day(p, date(2026, 9, 11)) is True


@pytest.mark.parametrize('change', [
    lambda p: p.replace(b'<EndDate>2026-12-19</EndDate>', b''),
    lambda p: p.replace(b'</DateRange>', b'<Provisional>true</Provisional></DateRange>'),
    lambda p: p.replace(b'</OperatingProfile>', b'<SpecialDaysOperation/></OperatingProfile>'),
    lambda p: p.replace(b'</ServicedOrganisation>', b'<ParentServicedOrganisationRef>X</ParentServicedOrganisationRef></ServicedOrganisation>'),
    lambda p: p.replace(b'<OrganisationCode>UOB', b'<OrganisationCode>OTHER'),
    lambda p: p.replace(b'</WorkingDays>', b'<DateExclusion>2026-09-15</DateExclusion></WorkingDays>'),
    lambda p: p.replace(b'</ServicedOrganisationRef>', b'</ServicedOrganisationRef><ServicedOrganisationRef>OTHER</ServicedOrganisationRef>', 1),
])
def test_unsupported_rules_never_become_proven_nonoperation(change):
    assert operating_day(change(profile()), date(2026, 9, 15)) is None


def test_holidays_and_unpublished_future_school_dates_are_untouched():
    assert operating_day(profile(), date(2026, 12, 24)) is None
    assert operating_day(profile(), date(2027, 2, 1)) is None


def test_negative_requires_exact_evidence_and_all_latest_witnesses_to_agree():
    p = REGULAR.replace(b'{}', b'').replace(b'MondayToSaturday', b'Friday')
    w = Evidence(('registered', 'X11'), date(2026, 9, 6), None, p, 'archive', 'hash', 'source.xml')
    day = date(2026, 9, 10)
    assert nonoperation_witnesses(day, [w]) == [w]
    assert not nonoperation_witnesses(day, [])
    assert not nonoperation_witnesses(day, [w, replace(w, profile=p.replace(b'Friday', b'Thursday'))])
    assert not nonoperation_witnesses(day, [w, replace(w, scope=('other', 'X11'))])
    assert not nonoperation_witnesses(day, [replace(w, start=date(2026, 9, 13))])
    assert not nonoperation_witnesses(day, [replace(w, end=date(2026, 9, 9))])


def test_school_collision_keeps_working_trip_and_records_holiday_exclusion(tmp_path, monkeypatch):
    import timetable_duplicate_evidence as duplicate
    p = tmp_path / 'tt.db'
    c = sqlite3.connect(p)
    c.executescript('''CREATE TABLE agency(agency_id,agency_noc);
    CREATE TABLE routes(route_id,agency_id,route_short_name);
    CREATE TABLE trips(trip_id,route_id,service_id,direction_id,vehicle_journey_code);
    CREATE TABLE stop_times(trip_id,stop_id,arrival_time,departure_time,stop_sequence);
    CREATE TABLE calendar(service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date);
    CREATE TABLE calendar_dates(service_id,date,exception_type);
    INSERT INTO agency VALUES('a','FBRI'); INSERT INTO routes VALUES('r','a','5');
    INSERT INTO trips VALUES('holiday','r','hol',0,'H'),('working','r','work',0,'W');
    INSERT INTO calendar_dates VALUES('hol','20260912',1),('work','20260912',1);
    ''')
    calls = (('A','08:00:00','08:00:00'),('B','08:30:00','08:30:00'))
    for trip in ['holiday','working']:
        c.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?)',
                      [(trip,*call,i) for i,call in enumerate(calls)])
    c.commit()
    evidence = {('5',0,code,calls):[Evidence(('registered','5'),date(2026,9,6),None,
                  profile(kind),'archive','file','source.xml')]
                for code,kind in [('H','Holidays'),('W','WorkingDays')]}
    monkeypatch.setattr(duplicate,'source_evidence',lambda *_:(evidence,{('registered','5'):{date(2026,9,6)}}))
    assert duplicate.reconcile_database(p,tmp_path) == {'trips_corrected':1,'dates_excluded':1}
    assert c.execute("SELECT service_id FROM trips WHERE trip_id='working'").fetchone()[0] == 'work'
    assert c.execute('SELECT trip_id,date FROM calendar_nonoperation_corrections').fetchall() == [('holiday','20260912')]
    assert c.execute('SELECT count(*) FROM stop_times').fetchone()[0] == 4
    assert c.execute('SELECT count(*) FROM calendar').fetchone()[0] == 0
    assert duplicate.reconcile_database(p,tmp_path) == {'trips_corrected':0,'dates_excluded':0}
    c.close()
