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
    groups = {key: dict(readings=0,on_time=0,available=True)
              for key in ['frequent','non_frequent','unclassified']}
    for row in rows:
        key = 'frequent' if row.get('frequent') is True else (
              'non_frequent' if row.get('frequent') is False else 'unclassified')
        group = groups[key]
        group['readings'] += int(row.get('readings_in_gate') or 0)
        group['on_time'] += int(row.get('on_time') or 0)
        group['available'] &= row.get('on_time_pct') is not None
    for group in groups.values():
        available = group.pop('available')
        group['on_time_pct'] = (round(100*group['on_time']/group['readings'],1)
                               if group['readings'] and available else None)
    return dict(metric='timing_point_adherence',groups=groups,
                caveat='Frequent-service results measure timetable adherence, not excess waiting time. Unclassified means the retained schedule cannot support the frequency label.')
