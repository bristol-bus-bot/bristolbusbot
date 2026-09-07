"""Shared evidence support for descriptive punctuality, never cancellations."""
from collections import defaultdict
import json
import math
import sqlite3

from audit_operators import SHOW_OPERATORS, NETWORK_LABEL
from audit_geo import geo_for
from audit_fleet import fleet_for

from sample_rules import qualification


def init_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS daily_sample_support (
        service_date TEXT NOT NULL,operator TEXT NOT NULL,scope TEXT NOT NULL,
        scope_key TEXT NOT NULL,support_json TEXT NOT NULL,
        PRIMARY KEY(service_date,operator,scope,scope_key))''')


def write_day(c,day,geo_index,fleet_index=None):
    """Preserve small cluster summaries before raw retention; no raw rewrites."""
    required={'trip_id','vehicle_ref','siri_journey_ref','stop_sequence','recorded_at',
              'observed_delay_s','gps_distance_m','is_origin','stop_code','operator','route'}
    cols={r[1] for r in c.execute('PRAGMA table_info(timepoint_observations)')}
    if not required.issubset(cols):
        init_schema(c)
        c.execute('DELETE FROM daily_sample_support WHERE service_date=?',(day,))
        return 0
    rows=c.execute('''SELECT operator,route,trip_id,vehicle_ref,siri_journey_ref,
        stop_sequence,recorded_at,observed_delay_s,stop_code
        FROM timepoint_observations WHERE service_date=? AND COALESCE(is_origin,0)=0
        AND gps_distance_m BETWEEN 0 AND 150 AND observed_delay_s IS NOT NULL
        ORDER BY operator,trip_id,vehicle_ref,siri_journey_ref,stop_sequence''',(day,)).fetchall()
    journeys=defaultdict(list)
    for row in rows:
        if row[0] in SHOW_OPERATORS:journeys[tuple(row[:1])+tuple(row[2:5])].append(tuple(row))
    cells=defaultdict(lambda:defaultdict(list))
    bad=set();missing=set()
    for identity,items in journeys.items():
        if not all(identity):missing.add(identity)
        clocks=[x[6] for x in items]
        # ISO input normalisation is essential: lexical UTC-offset comparisons
        # would manufacture inversions. Missing times cannot certify order.
        from datetime import datetime
        try:
            parsed=[datetime.fromisoformat(t.replace('Z','+00:00')) for t in clocks]
            if any(t.tzinfo is None for t in parsed):raise ValueError('missing timezone')
            times=[t.timestamp() for t in parsed]
        except (ValueError,AttributeError,TypeError):
            times=[];missing.add(identity)
        if any(b<a for a,b in zip(times,times[1:])):bad.add(identity)
        if len({x[1] for x in items})>1:bad.add(identity)
        for row in items:
            op,route,_,_,_,_,_,delay,stop=row
            scopes=[('overall',''),('route',route or '')]
            fleet = fleet_for(fleet_index, op, identity[2]) if fleet_index else None
            if fleet and fleet.get('model'):
                scopes.append(('fleet',fleet['model']))
            place=geo_for(geo_index,stop) if geo_index else None
            if place:
                scopes.extend((kind,place[kind]) for kind in ['area','ward'] if place.get(kind))
                scopes.extend((kind+'_route',json.dumps([place[kind],route or '']))
                              for kind in ['area','ward'] if place.get(kind))
            for label in [op,NETWORK_LABEL]:
                for scope,key in scopes:cells[(label,scope,key)][identity].append(delay)
    init_schema(c)
    c.execute('DELETE FROM daily_sample_support WHERE service_date=?',(day,))
    for (op,scope,key),groups in cells.items():
        support=dict(readings=sum(len(v) for v in groups.values()),
                     on_time=sum(sum(-60<=v<=359 for v in values) for values in groups.values()),
                     journeys=len(groups),squared_weights=sum(len(v)**2 for v in groups.values()),
                     inconsistent_journeys=len(set(groups)&bad),missing_identity=len(set(groups)&missing),
                     ambiguous_readings=sum(len(values) for identity,values in groups.items() if identity in bad),
                     ambiguous_on_time=sum(sum(-60<=v<=359 for v in values) for identity,values in groups.items() if identity in bad),
                     service_days=1)
        c.execute('INSERT INTO daily_sample_support VALUES (?,?,?,?,?)',
                  (day,op,scope,key,json.dumps(support,sort_keys=True)))
    return len(cells)


def read_support(c,days,operator,scope='overall',key=''):
    if not days or not c.execute("SELECT 1 FROM sqlite_master WHERE name='daily_sample_support' AND type='table'").fetchone():return None
    keys=list(key) if isinstance(key,(list,tuple)) else [key]
    if not keys:return None
    rows=c.execute('SELECT service_date,support_json FROM daily_sample_support '
                   'WHERE operator=? AND scope=? AND scope_key IN ('+','.join('?'*len(keys))+') AND service_date IN ('+
                   ','.join('?'*len(days))+')',(operator,scope,*keys,*days)).fetchall()
    if {r[0] for r in rows}!=set(days):return None
    total=defaultdict(int)
    for _,raw in rows:
        for k,v in json.loads(raw).items():total[k]+=v
    total['service_days']=len({r[0] for r in rows})
    return dict(total)


def qualify_row(c,days,operator,row,scope='overall',key='',coverage_verified=False):
    support=read_support(c,days,operator,scope,key)
    if support is not None and support['readings']!=row.get('readings_in_gate',row.get('readings',0)):
        support['rollup_mismatch']=1
    if support is not None and 'on_time' in row and support['on_time'] != row['on_time']:
        support['rollup_mismatch']=1
    q=qualification(support,coverage_verified=coverage_verified)
    row['qualification']={key:q[key] for key in
                          ('version','status','reasons','journeys','service_days','range_pct','ambiguous_readings')
                          if key in q}
    # Only the weekly overall/fleet consumers need composable moments.
    if scope in ('overall','fleet'):
        row['sample_support']=support
    if q['status']=='unavailable':
        for field in ['on_time_pct','mean_delay_s','median_delay_s']:
            if field in row:row[field]=None
    return q
