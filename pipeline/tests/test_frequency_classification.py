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
    rollup_frequency(c,'20260907',['ABUS'],'ABUS')
    # Every row is filed under the label it was rolled up for, never a route.
    assert sorted(r[0] for r in c.execute('SELECT DISTINCT operator FROM daily_route_class'))==['ABUS','ALL']
    assert list(c.execute("SELECT route FROM daily_route_class WHERE operator='ABUS'"))==[('shared-number',)]
    actual=dict(c.execute("SELECT route,frequent FROM daily_route_class WHERE operator='ALL'"))
    # The pooled view keeps each operator's route 'shared-number' apart.
    assert actual=={'half-hourly':0,'frequent':1,'mixed':None,'shared-number':0,
                    'shared-number Abus':0}


def test_fleet_rows_are_filed_under_their_rollup_label_with_tagged_pooled_routes():
    import json
    from audit_rollup import rollup_fleet
    c=sqlite3.connect(':memory:')
    c.executescript('''CREATE TABLE timepoint_observations(service_date,operator,route,vehicle_ref,
                          observed_delay_s,is_origin,gps_distance_m);
                      CREATE TABLE daily_fleet_summary(service_date,operator,model,electric,fuel,vehicles,
                          readings_in_gate,on_time,on_time_pct,mean_delay_s,median_delay_s,routes_json,
                          PRIMARY KEY(service_date,operator,model));''')
    for op,route,veh in [('FBRI','13','FBRI-101'),('SSWL','13','SSWL-201')]:
        c.execute('INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?)',
                  ('20260907',op,route,veh,30,0,10))
    fleet={('FBRI','101'):dict(model='Enviro400',electric=False,fuel='diesel'),
           ('SSWL','201'):dict(model='Enviro400',electric=False,fuel='diesel')}
    rollup_fleet(c,'20260907',['FBRI','SSWL'],'ALL',fleet)
    rollup_fleet(c,'20260907',['SSWL'],'SSWL',fleet)
    rows={op:json.loads(routes) for op,routes in c.execute(
        'SELECT operator,routes_json FROM daily_fleet_summary')}
    assert set(rows)=={'ALL','SSWL'}
    assert sorted(r for r,_ in rows['ALL'])==['13','13 Stagecoach']
    assert rows['SSWL']==[['13',1]]
