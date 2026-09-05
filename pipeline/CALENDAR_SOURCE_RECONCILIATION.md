# Calendar removal exceptions

The regional GTFS calendar used in the 5 September 2026 build contains a
1 December removal exception that contradicts ordinary weekday operation in
First's original TransXChange. This prevented a newer timetable from passing
the existing forward coverage check.

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

Parsing or transaction failures abort the build. No acceptance threshold or
promotion rule changes. A candidate with unresolved coverage loss still fails
the full comparison, even if this reconciliation fixes another date.

## Observed validation

An offline copy of build 33943451670 gained 5,750 source-proven calendar
corrections, all on 1 December 2026. Its original schedules, route inventory and
stop times did not change. The X4 10:17, U2 10:55 and 126 11:00 saved examples
continue to match the correct direction and endpoints.

The full comparison against the 16 August accepted timetable then exposed four
remaining failures: 24 and 26 December 2026, and 29 March and 3 May 2027. These
are not repaired by this module and are not grounds for bypassing validation.
This change alone does not make that artifact suitable for promotion.
