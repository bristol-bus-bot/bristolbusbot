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
