# Calendar removal exceptions

Regional GTFS removal exceptions can contradict ordinary weekday operation
in the original operator TransXChange. Source reconciliation repairs only
exclusions for which the original timetable supplies positive evidence.

`timetable_calendar_evidence.py` runs on a disposable candidate before edition
normalization. It can remove a GTFS exclusion only when all applicable source
witnesses confirm the ordinary operating day. Identity requires First Bristol's
operator code, public route, direction, vehicle journey code, and the complete
ordered stop/arrival/departure sequence. Source edition selection uses the latest
start date for the registered service and line that has begun by the queried day.
Expired source periods cannot provide evidence.

Only simple regular-day profiles are supported. Special-date, organisation,
periodic, unknown and conflicting rules do not authorize a correction. Public
holidays, Christmas Eve and New Year's Eve remain excluded from this repair.
This restriction is intentional: an ordinary weekday schedule cannot establish
holiday operation. Missing positive holiday services are a separate problem.

Corrections clone calendars for only the proven journeys. Other trips sharing
the original calendar, legitimate removal exceptions and added-service dates
are preserved. `calendar_source_corrections` records each changed trip/date,
original and corrected calendar IDs, archive and XML hashes, source member,
service edition and profile hash. The parcel's database hash also covers these
records. The build's existing source manifest retains the downloaded archives'
provenance. Historical databases and observations are never modified.

Parsing or transaction failures abort the build. Ordinary-date coverage losses
still fail the full comparison, even if reconciliation fixes another date.

## Distant holiday coverage

The separately recorded `service-window-v2` acceptance policy permits a failed
forward comparison only on a recognised recurring England/Wales holiday or
Christmas/New Year's Eve more than 56 days away. No timetable journeys are
added by this policy. Numeric inventory, near-term and ordinary-date thresholds
are unchanged; a run of missing normal days still fails.

Every provisional date and metric is recorded with its original acceptance
floor and an eight-week review deadline. The last accepted promotion retains
these obligations across failed attempts and subsequent timetable updates.
Re-comparison uses the original floor even if the live timetable is already
sparse on that date. Recovered coverage clears the obligation; unresolved
coverage within 56 days blocks acceptance. The existing estate monitor also
raises an incident at the deadline even if no new build runs.
# Identical journey replacements

The disposable build also checks overlapping identities with identical complete
stop calls, arrival/departure times, pickup/drop-off permissions, registered
route and direction. It retires the earlier identity on a date only when the
latest First source edition supplies the replacement and omits the earlier
identity. All trip and stop-time rows remain intact; dated calendar exclusions
and source-file hashes record the correction.

For simple profiles the replacement must positively operate on that date.
Identical complete profiles, including appended organisation definitions, can
also prove that two already-active GTFS identities impose identical conditions.
This second case does not add operating dates or interpret school calendars:
the identical replacement must remain active. Different or conflicting profiles
remain unresolved. Raw inventory, source validation and promotion gates still
apply.

Regular weekday masks are compared for the particular overlapping day: a
Tuesday-Thursday mask and a Monday-Thursday mask have the same remaining
conditions on Tuesday. All school-calendar definitions and other conditions
must still agree. Services represented entirely by `calendar_dates` are handled
without adding any operating dates.

Several GTFS IDs for one positively operating, uniquely declared source journey
can share one deterministic surviving identity on overlapping dates. Different
source journey codes are not treated as aliases. Replacement receipts follow
any alias chain to the surviving identity; cycles refuse the build.

Supplemented journeys retain their original source journey code and complete
source calls, including calls outside the regional coordinate inventory.
Reconciliation requires both identical stored calls and identical full source
calls, so different full journeys cannot be merged just because their local
stop subsets happen to match.

## Explicit weekday and university-calendar exclusions

For an identical-schedule collision, an exact source journey can also prove
that one ID does not operate on the date in question. The ordinary-day evaluator
checks its regular weekday mask and a single supported organisation rule with
explicit, inclusive working-date ranges. Under the
[TXC-PTI profile, sections 3.2 and 9.3](https://pti.org.uk/system/files/files/TransXChange%20UK%20PTI%20Profile%20v1.1.pdf),
days outside working ranges are holidays. Dates after the last supplied term,
public holidays, provisional ranges, missing organisations, conflicting source
witnesses and unsupported overrides remain unproven.

This exclusion needs the exact journey code and complete source calls; absence
from a newer file is not evidence. All newest available declarations of that
exact journey must explicitly agree on non-operation. Only dates with an actual
schedule collision are considered. Lone special-date services are untouched.
Receipts are stored in `calendar_nonoperation_corrections`, separately from
replacement receipts. Trips and stop calls remain intact, and exception-only
services retain their original representation. Delivery validation is unchanged.

Supplemental journey IDs include the line and source edition start as well as
the operator journey code. Reusing a code in a later source file must not cause
that later timetable to be skipped; all editions reach the existing window
normalizer before duplicate/calendar reconciliation.

## Source editions from First's TXC, not GTFS dates (September 2026)

BODS regional GTFS rewrites calendars: once a newer edition has started, every
edition's `start_date` becomes the feed date and open-ended editions receive a
synthetic end about nine months later. On 28 September 2026 this collapsed
superseded route editions from 86 to 3. It left 2,596 identical First journeys
active twice every Monday to Thursday and a withdrawn bank-holiday-week
edition running every Monday. First also renumbers every journey code in each
edition, so the exact-code repair above stopped working as soon as First
published a 4 October edition that the GTFS did not yet carry.

`timetable_source_index.py` parses First's TXC once. A GTFS trip's lineage is
every edition declaring its exact line, direction, code and complete calls. An
edition is *represented* in the GTFS when trips matching it (and not also its
predecessor) cover at least a quarter of its journeys. Three rules use this, and
only when the build supplies the index:

* **Successor edition** (`successor_witness`, calendar repair). When the edition
  in force on the day is the very next edition after the trip's lineage and is
  not yet represented, exactly one journey there with identical complete calls
  must positively operate under `ordinary_operating_day`. Every other
  identical-schedule journey there must return `operating_day() is False`. Each
  successor journey may be claimed by one GTFS trip only; ambiguous claims prove
  nothing. Receipts carry `role: predecessor` and `role: successor` witnesses.
* **Duplicate lineage** (`lineage_duplicate_proof`, duplicate reconciliation).
  Inside an identical-journey group, on days both copies are active, retire:
  a copy no available edition declares (`basis: orphan_duplicate`), or a copy
  whose lineage is older than its identical twin's in the same scope
  (`basis: superseded_duplicate`). The survivor carries exactly the same stop
  service, so no scheduled service is removed.
* **Superseded non-operation** (`superseded_nonoperation`). A trip whose whole
  lineage is older than the represented edition in force on the day is excluded
  on that day only when that edition's identical-schedule journeys all
  explicitly do not operate. A missing schedule, unknown or unsupported profiles
  never count. Receipts use `reason: superseded_edition_nonoperation`.

`timetable_quality_gates.py` refuses candidates in which more than 0.5% of a
day's active First trips duplicate another active trip's complete schedule
under a different service. It also refuses when the next 42 days of distinct
First schedules fall outside 85%-110% of First's in-force TXC count (unknown
profiles widen the upper bound). The Pi comparison adds `candidate_service_inflation`
(policy `service-window-v3`) for operators growing more than 25% on three
near-term days without new routes.
