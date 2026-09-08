from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import timetable_duplicate_evidence as duplicate
from timetable_calendar_evidence import Evidence

DAY=date(2026,9,6)
PROFILE=b'<OperatingProfile><RegularDayType><DaysOfWeek><Sunday/></DaysOfWeek></RegularDayType></OperatingProfile>'
OLD=Evidence(('registered','77'),date(2026,8,30),None,PROFILE,'archive','oldhash','old.xml')
NEW=replace(OLD,start=DAY,file_sha256='newhash',member='new.xml')


def test_identical_source_replacement_must_be_latest_operating_edition():
    editions={OLD.scope:{OLD.start,NEW.start}}
    assert duplicate.replacement_proof(DAY,'old','new',{'old':[OLD],'new':[NEW]},editions)
    future = replace(OLD,start=date(2026,9,13))
    assert duplicate.replacement_proof(DAY,'old','new',{'old':[OLD,future],'new':[NEW]},
                                       {OLD.scope:editions[OLD.scope]|{future.start}})
    assert not duplicate.replacement_proof(DAY,'old','new',{'old':[OLD,NEW],'new':[NEW]},editions)
    assert not duplicate.replacement_proof(DAY,'old','new',{'old':[OLD],'new':[replace(NEW,profile=b'<Unknown/>')]},editions)
    assert not duplicate.replacement_proof(DAY,'old','new',{'old':[OLD],'new':[replace(NEW,scope=('different','77'))]}, {**editions,('different','77'):{DAY}})


@pytest.mark.parametrize('date_only', [False, True])
def test_only_proven_old_dates_are_removed_and_stop_inventory_is_unchanged(tmp_path,monkeypatch,date_only):
    path=tmp_path/'tt.db'
    c=sqlite3.connect(path)
    c.executescript('''CREATE TABLE agency(agency_id,agency_noc);
    CREATE TABLE routes(route_id,agency_id,route_short_name);
    CREATE TABLE trips(trip_id,route_id,service_id,direction_id,vehicle_journey_code);
    CREATE TABLE stop_times(trip_id,stop_id,arrival_time,departure_time,stop_sequence,pickup_type,drop_off_type);
    CREATE TABLE calendar(service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date);
    CREATE TABLE calendar_dates(service_id,date,exception_type);
    INSERT INTO agency VALUES('a','FBRI'); INSERT INTO routes VALUES('r','a','77');
    INSERT INTO trips VALUES('old','r','oldcal',0,'old'),('new','r','newcal',0,'new'),('short','r','oldcal',0,'short');
    INSERT INTO calendar VALUES('oldcal',0,0,0,0,0,0,1,'20260830','20260913'),('newcal',0,0,0,0,0,0,1,'20260906','20260913');
    INSERT INTO calendar_dates VALUES('oldcal','20260907',1);
    ''')
    if date_only:
        c.execute('DELETE FROM calendar')
        c.executemany('INSERT INTO calendar_dates VALUES (?,?,1)',
                      [('oldcal','20260830'),('oldcal','20260906'),('oldcal','20260913'),
                       ('newcal','20260906'),('newcal','20260913')])
    for trip in ['old','new','short']:
        c.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)',
          [(trip,'A','08:00:00','08:00:00',0,0,0),(trip,'C' if trip=='short' else 'B','08:30:00','08:30:00',1,0,0)])
    c.commit()
    schedule=(('A','08:00:00','08:00:00'),('B','08:30:00','08:30:00'))
    monkeypatch.setattr(duplicate,'source_evidence',lambda *_:({('77',0,'old',schedule):[OLD],('77',0,'new',schedule):[NEW]}, {OLD.scope:{OLD.start,NEW.start}}))
    assert duplicate.reconcile_database(path,tmp_path)=={'trips_corrected':1,'dates_excluded':2}
    clone=c.execute("SELECT service_id FROM trips WHERE trip_id='old'").fetchone()[0]
    expected=[('20260906',2),('20260907',1),('20260913',2)]
    if date_only:expected.insert(0,('20260830',1))
    assert c.execute('SELECT date,exception_type FROM calendar_dates WHERE service_id=? ORDER BY date',(clone,)).fetchall()==expected
    if date_only:
        assert c.execute('SELECT count(*) FROM calendar').fetchone()[0] == 0
    assert c.execute("SELECT service_id FROM trips WHERE trip_id='short'").fetchone()[0]=='oldcal'
    assert c.execute('SELECT count(*) FROM stop_times').fetchone()[0]==6
    assert duplicate.reconcile_database(path,tmp_path)=={'trips_corrected':0,'dates_excluded':0}


def test_identical_school_rules_can_prove_replacement_without_inventing_operating_days():
    profile=PROFILE.replace(b'</OperatingProfile>',b'<ServicedOrganisationDayType/></OperatingProfile>')
    old=replace(OLD,profile=profile);new=replace(NEW,profile=profile)
    editions={OLD.scope:{OLD.start,NEW.start}}
    assert duplicate.replacement_proof(DAY,'old','new',{'old':[old],'new':[new]},editions)
    assert not duplicate.replacement_proof(DAY,'old','new',
      {'old':[old],'new':[replace(new,profile=profile+b'<Different/>')]},editions)


def test_weekday_expansion_with_identical_school_conditions_proves_only_shared_days():
    profile=PROFILE.replace(b'<Sunday/>',b'<Tuesday/><Wednesday/><Thursday/>').replace(
        b'</OperatingProfile>',b'<ServicedOrganisationDayType><School>A</School></ServicedOrganisationDayType></OperatingProfile>')
    old=replace(OLD,profile=profile)
    new=replace(NEW,profile=profile.replace(b'<Tuesday/>',b'<Monday/><Tuesday/>'))
    editions={OLD.scope:{OLD.start,NEW.start}}
    assert duplicate.replacement_proof(date(2026,9,8),'old','new',{'old':[old],'new':[new]},editions)
    assert not duplicate.replacement_proof(date(2026,9,7),'old','new',{'old':[old],'new':[new]},editions)
    changed=replace(new,profile=new.profile.replace(b'<School>A</School>',b'<School>B</School>'))
    assert not duplicate.replacement_proof(date(2026,9,8),'old','new',{'old':[old],'new':[changed]},editions)


def test_alias_requires_one_positive_latest_source_declaration_and_a_journey_code():
    key=('77',0,'VJ1',())
    editions={OLD.scope:{OLD.start,NEW.start}}
    assert duplicate.same_source_alias_proof(DAY,key,{key:[OLD,NEW]},editions)==[NEW]
    assert not duplicate.same_source_alias_proof(DAY,key,{key:[NEW,NEW]},editions)
    missing=('77',0,None,())
    assert not duplicate.same_source_alias_proof(DAY,missing,{missing:[NEW]},editions)


@pytest.mark.parametrize('different_source_calls', [False, True])
def test_three_gtfs_aliases_keep_one_active_replacement_and_preserve_distinct_journeys(tmp_path,monkeypatch,different_source_calls):
    path=tmp_path/'aliases.db'
    c=sqlite3.connect(path)
    c.executescript('''CREATE TABLE agency(agency_id,agency_noc);
    CREATE TABLE routes(route_id,agency_id,route_short_name);
    CREATE TABLE trips(trip_id,route_id,service_id,direction_id,vehicle_journey_code);
    CREATE TABLE stop_times(trip_id,stop_id,arrival_time,departure_time,stop_sequence,pickup_type,drop_off_type);
    CREATE TABLE calendar(service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date);
    CREATE TABLE calendar_dates(service_id,date,exception_type);
    INSERT INTO agency VALUES('a','FBRI'); INSERT INTO routes VALUES('r','a','77');
    INSERT INTO calendar VALUES('c',0,0,0,0,0,0,1,'20260906','20260906');''')
    for trip,code in [('a','VJ1'),('b','VJ1'),('c','VJ1'),('separate','VJ2')]:
        c.execute('INSERT INTO trips VALUES (?,?,?,0,?)',(trip,'r','c',code))
        c.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)',
          [(trip,'A','08:00:00','08:00:00',0,0,0),(trip,'B','08:30:00','08:30:00',1,0,0)])
    if different_source_calls:
        import json
        c.execute('CREATE TABLE supplement_trip_sources(trip_id,full_calls_json)')
        for trip in ['a','b','c']:
            calls=[('A','08:00:00','08:00:00'),('B','08:30:00','08:30:00')]
            if trip=='b':calls.insert(1,('outside','08:15:00','08:15:00'))
            c.execute('INSERT INTO supplement_trip_sources VALUES (?,?)',(trip,json.dumps(calls)))
    c.commit()
    schedule=(('A','08:00:00','08:00:00'),('B','08:30:00','08:30:00'))
    monkeypatch.setattr(duplicate,'source_evidence',lambda *_:({
        ('77',0,'VJ1',schedule):[NEW],('77',0,'VJ2',schedule):[NEW]}, {NEW.scope:{DAY}}))
    n=1 if different_source_calls else 2
    assert duplicate.reconcile_database(path,tmp_path)=={'trips_corrected':n,'dates_excluded':n}
    import json
    assert {json.loads(row[0])['replacement_trip_id'] for row in c.execute(
        'SELECT evidence_json FROM duplicate_source_corrections')}=={'a'}
    active=[('a',),('b',),('separate',)] if different_source_calls else [('a',),('separate',)]
    assert c.execute("SELECT trip_id FROM trips WHERE service_id='c' ORDER BY 1").fetchall()==active
    assert c.execute('SELECT count(*) FROM trips').fetchone()[0]==4


def test_replacement_receipts_follow_alias_chains_and_refuse_cycles():
    corrections={'old':{'day':dict(replacement_trip_id='alias',witnesses=['older'])},
                 'alias':{'day':dict(replacement_trip_id='survivor',witnesses=['latest'])}}
    duplicate.resolve_replacements(corrections)
    assert corrections['old']['day']==dict(replacement_trip_id='survivor',witnesses=['older','latest'])
    cycle={'a':{'day':dict(replacement_trip_id='b',witnesses=[])},
           'b':{'day':dict(replacement_trip_id='a',witnesses=[])}}
    with pytest.raises(RuntimeError,match='cyclic'):
        duplicate.resolve_replacements(cycle)
