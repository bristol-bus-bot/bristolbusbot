import sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit_rollup import rollup_frequency


def test_frequency_does_not_pool_directions_operators_or_mixed_patterns():
    c=sqlite3.connect(':memory:')
    c.executescript('''CREATE TABLE expected_trips(service_date,operator,route,first_departure,route_id,direction);
                      CREATE TABLE daily_route_class(service_date,operator,route,frequent,peak_hour_journeys);''')
    for route,counts in [('half-hourly',(2,2)),('frequent',(6,6)),('mixed',(6,2))]:
        for direction,count in enumerate(counts):
            for n in range(count):
                c.execute('INSERT INTO expected_trips VALUES (?,?,?,?,?,?)',
                          ('20260907','FBRI',route,f'08:{n*10:02}:00',route,direction))
    for op in ['FBRI','ABUS']:
        for n in range(3):
            c.execute('INSERT INTO expected_trips VALUES (?,?,?,?,?,?)',
                      ('20260907',op,'shared-number',f'08:{n*20:02}:00',op,0))
    rollup_frequency(c,'20260907',['FBRI','ABUS'],'ALL')
    actual=dict(c.execute('SELECT route,frequent FROM daily_route_class'))
    assert actual=={'half-hourly':0,'frequent':1,'mixed':None,'shared-number':0}
