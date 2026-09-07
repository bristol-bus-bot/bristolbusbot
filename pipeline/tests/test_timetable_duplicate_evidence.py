from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3
import sys
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


def test_only_proven_old_dates_are_removed_and_stop_inventory_is_unchanged(tmp_path,monkeypatch):
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
    for trip in ['old','new','short']:
        c.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)',
          [(trip,'A','08:00:00','08:00:00',0,0,0),(trip,'C' if trip=='short' else 'B','08:30:00','08:30:00',1,0,0)])
    c.commit()
    schedule=(('A','08:00:00','08:00:00'),('B','08:30:00','08:30:00'))
    monkeypatch.setattr(duplicate,'source_evidence',lambda *_:({('77',0,'old',schedule):[OLD],('77',0,'new',schedule):[NEW]}, {OLD.scope:{OLD.start,NEW.start}}))
    assert duplicate.reconcile_database(path,tmp_path)=={'trips_corrected':1,'dates_excluded':2}
    clone=c.execute("SELECT service_id FROM trips WHERE trip_id='old'").fetchone()[0]
    assert c.execute('SELECT date,exception_type FROM calendar_dates WHERE service_id=? ORDER BY date',(clone,)).fetchall()==[('20260906',2),('20260907',1),('20260913',2)]
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
