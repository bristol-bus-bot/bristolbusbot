"""Portable journey sample rules shared by audit and social products."""
import math

VERSION = 1


def qualification(support, *, coverage_verified=False):
    """Treat all readings within a journey as one dependent cluster.

    The range is a conservative Hoeffding sampling bound for independent,
    bounded journey clusters with unequal weights. It does not cover feed
    bias, wrong matches, or correlation between journeys. Those limitations
    are reported separately rather than hidden by a large reading count.
    """
    if not support:
        return dict(version=VERSION,status='unavailable',reasons=['sample_support_unavailable'],
                    journeys=None,service_days=None,range_pct=None)
    n = support['readings']
    j = support['journeys']
    squares = support['squared_weights']
    reasons = []
    impossible_weights = (j > 0 and squares + 1e-8 < n*n/j) or squares > n*n
    if (n < 0 or j < 0 or j > n or not 0 <= support['on_time'] <= n
            or squares < 0 or impossible_weights):
        return dict(version=VERSION,status='unavailable',reasons=['invalid_sample_counts'],
                    journeys=None,service_days=None,range_pct=None)
    if not n:
        reasons.append('no_eligible_readings')
    if j < 2:
        reasons.append('fewer_than_two_journeys')
    ambiguous = support.get('ambiguous_readings', 0)
    ambiguous_on_time = support.get('ambiguous_on_time', 0)
    if support.get('inconsistent_journeys') and 'ambiguous_readings' not in support:
        reasons.append('inconsistent_journey_order')
    if n and ambiguous >= n:
        reasons.append('all_readings_have_ambiguous_order')
    if support.get('missing_identity'):
        reasons.append('journey_identity_unavailable')
    if support.get('rollup_mismatch'):
        reasons.append('raw_support_differs_from_rollup')
    effective = n*n/squares if squares else 0
    radius = math.sqrt(math.log(40)/(2*effective)) if effective else 1
    # For known order-ambiguous journeys, allow every associated reading to
    # fall in either category. Do not pretend the suspect assignment is fixed.
    lower=(support['on_time']-ambiguous_on_time)/n if n else 0
    upper=(support['on_time']-ambiguous_on_time+ambiguous)/n if n else 1
    bounds=[round(100*max(0,lower-radius),1),round(100*min(1,upper+radius),1)] if n else None
    status = 'unavailable' if reasons else 'supported'
    if status != 'unavailable':
        if radius > .10:
            reasons.append('wide_sampling_range')
        if support.get('inconsistent_journeys'):
            reasons.append('inconsistent_journey_order')
        if not coverage_verified:
            reasons.append('coverage_unverified')
        if support.get('service_days',0) < 2:
            reasons.append('single_service_day')
        if reasons:
            status = 'indicative'
    return dict(version=VERSION,status=status,reasons=reasons,readings=n,journeys=j,
                service_days=support.get('service_days',0),effective_journeys=round(effective,1),
                ambiguous_readings=ambiguous,
                range_pct=bounds,range_method='95pct_journey_cluster_bound_plus_known_order_sensitivity',
                caveat='Assumes independent journeys; shared disruption, feed bias and other assignment errors are not included.')
