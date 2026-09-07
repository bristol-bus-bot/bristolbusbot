import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sample_quality import qualification
from sample_quality import write_day, read_support, qualify_row
import sqlite3


def sample(j,n=1000):
    return dict(readings=n,on_time=n//2,journeys=j,squared_weights=n*n/j,service_days=7)


def test_many_stops_from_one_journey_do_not_become_a_large_sample():
    one=qualification(sample(1),coverage_verified=True)
    many=qualification(sample(1000),coverage_verified=True)
    assert one['status']=='unavailable'
    assert one['effective_journeys']==1
    assert many['status']=='supported'
    assert many['range_pct'][1]-many['range_pct'][0]<10


def test_missing_empty_and_unverifiable_history_never_become_zero_percent():
    assert qualification(None)['range_pct'] is None
    assert qualification(dict(readings=0,on_time=0,journeys=0,squared_weights=0,service_days=0))['status']=='unavailable'
    bad=sample(1000);bad['inconsistent_journeys']=1
    assert qualification(bad,coverage_verified=True)['status']=='unavailable'


def test_coverage_and_days_are_not_overridden_by_a_large_count():
    assert qualification(sample(1000))['status']=='indicative'
    day=sample(1000);day['service_days']=1
    assert 'single_service_day' in qualification(day,coverage_verified=True)['reasons']


def test_precision_boundary_comes_from_range_not_thirty_readings():
    assert qualification(sample(184),coverage_verified=True)['status']=='indicative'
    assert qualification(sample(185),coverage_verified=True)['status']=='supported'


def test_retained_support_counts_journeys_and_detects_real_clock_inversion():
    c=sqlite3.connect(':memory:')
    c.execute('''CREATE TABLE timepoint_observations(service_date,operator,route,
      trip_id,vehicle_ref,siri_journey_ref,stop_sequence,recorded_at,
      observed_delay_s,gps_distance_m,is_origin,stop_code)''')
    c.executemany('INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',[
      ('20260906','FBRI','77','a','bus','0800',1,'2026-09-06T09:00:00+01:00',0,10,0,'A'),
      ('20260906','FBRI','77','a','bus','0800',2,'2026-09-06T08:01:00+00:00',600,10,0,'A'),
      ('20260906','FBRI','77','b','bus','0900',1,'2026-09-06T09:00:00+00:00',0,10,0,'A'),
      ('20260906','FBRI','77','b','bus','0900',2,'2026-09-06T09:01:00+00:00',0,10,0,'A'),
    ])
    write_day(c,'20260906',{'A':{'area':'Bristol','ward':'Central'}})
    support=read_support(c,['20260906'],'ALL')
    assert support['journeys']==2
    assert support['squared_weights']==8
    assert support['inconsistent_journeys']==0
    row={'readings':4,'on_time_pct':75.0}
    assert qualify_row(c,['20260906'],'ALL',row)['status']=='indicative'
    assert row['on_time_pct']==75.0
    assert read_support(c,['20260906'],'ALL','ward','Central')==support
    c.execute("UPDATE timepoint_observations SET recorded_at='2026-09-06T07:59:00+00:00' WHERE trip_id='a' AND stop_sequence=2")
    write_day(c,'20260906',{})
    q=qualify_row(c,['20260906'],'ALL',row)
    assert q['status']=='indicative'
    assert q['ambiguous_readings']==2
    assert 'inconsistent_journey_order' in q['reasons']
    assert row['on_time_pct']==75.0
