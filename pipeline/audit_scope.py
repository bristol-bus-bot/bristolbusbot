"""Describe observed evidence, without implying a census of bus services."""
from audit_operators import SHOW_OPERATORS, operator_name


def operator_composition(counts):
    """Reading-weighted composition for exactly the caller's selected scope."""
    counts = {op: max(0, int(counts.get(op, 0))) for op in SHOW_OPERATORS}
    total = sum(counts.values())
    return {
        'basis': 'eligible_timing_point_readings',
        'readings': total,
        'operators': [dict(code=op, name=operator_name(op), readings=n,
                           share_pct=round(100*n/total, 1) if total else None)
                      for op, n in counts.items()],
        'caveat': 'Shares describe captured readings, not market share, passengers or an even survey of the network. Operators and places without observations are not represented.',
    }


def frequency_adherence(rows):
    """On-time share by frequency class, from the routes that have a figure.

    A route without a usable figure (too few readings) no longer blanks its
    whole class; the class reports how many of its routes are counted."""
    groups = {key: dict(readings=0,on_time=0,routes=0,routes_counted=0,
                        counted_readings=0,counted_on_time=0)
              for key in ['frequent','non_frequent','unclassified']}
    for row in rows:
        key = 'frequent' if row.get('frequent') is True else (
              'non_frequent' if row.get('frequent') is False else 'unclassified')
        group = groups[key]
        readings = int(row.get('readings_in_gate') or 0)
        on_time = int(row.get('on_time') or 0)
        group['readings'] += readings
        group['on_time'] += on_time
        group['routes'] += 1
        if row.get('on_time_pct') is not None:
            group['routes_counted'] += 1
            group['counted_readings'] += readings
            group['counted_on_time'] += on_time
    for group in groups.values():
        counted_readings = group.pop('counted_readings')
        counted_on_time = group.pop('counted_on_time')
        group['on_time_pct'] = (round(100*counted_on_time/counted_readings,1)
                               if counted_readings else None)
    return dict(metric='timing_point_adherence',groups=groups,
                caveat='Frequent-service results measure timetable adherence, not excess waiting time. Unclassified means the retained schedule cannot support the frequency label.')
