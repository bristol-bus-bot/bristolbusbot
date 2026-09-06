import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit_scope import operator_composition, frequency_adherence


def test_composition_changes_with_selected_evidence_and_never_counts_pool_twice():
    a=operator_composition({'FBRI':90,'ABUS':10,'ALL':100})
    b=operator_composition({'FBRI':10,'ABUS':90})
    assert a['readings']==100
    assert next(x for x in a['operators'] if x['code']=='FBRI')['share_pct']==90
    assert next(x for x in b['operators'] if x['code']=='FBRI')['share_pct']==10
    assert all(x['share_pct'] is None for x in operator_composition({})['operators'])


def test_unknown_frequency_does_not_become_nonfrequent_or_zero_percent():
    groups=frequency_adherence([
        dict(frequent=None,readings_in_gate=10,on_time=8,on_time_pct=80),
        dict(frequent=True,readings_in_gate=2,on_time=1,on_time_pct=None)])['groups']
    assert groups['unclassified']['readings']==10
    assert groups['non_frequent']['on_time_pct'] is None
    assert groups['frequent']['on_time_pct'] is None
