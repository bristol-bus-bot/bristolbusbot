# Map timing presentation

The site reads the collector's existing journey match. It never chooses another
trip or writes these display calculations into live.db or audit.db.

`timingSource` distinguishes `collector`, `scheduled_wait`, `route_estimate`
and `unavailable`. Waiting has a null delay, `eventType=waiting`, and a scheduled
departure. `waitingAtOrigin` identifies the first scheduled row regardless of
its sequence number; intermediate dwell uses `waitingAtStop` instead.

Waiting requires a position within 75 metres of the stop, less than 90 seconds
old, before its departure. At the origin, the permitted lead is one hour. An
intermediate stop must have at least two minutes of scheduled dwell and the
position must be no earlier than five minutes before its scheduled arrival.
The final stop is excluded. Multiple eligible calls are left unclassified.
This is a map interpretation of a position before departure, not proof that
the bus remained stationary or an audit observation of departure.

Between-stop estimates only fill missing collector delays on already matched
trips whose nearest scheduled stop is over one kilometre away. Every scheduled
stop must fit a stored route line within 100 metres in increasing order. The
vehicle and its enclosing stops must have unambiguous projections. Ambiguity
elsewhere on a terminal loop is allowed only when a complete ordered stop path
still exists. Compatible route variants must agree within one minute; missing,
incompatible or ambiguous geometry produces no estimate. Segments are limited
to 30 kilometres and one hour. Position advances proportionally between the
previous stop's departure and the next stop's arrival. Existing -15/+90-minute
sanity bounds remain. The result is explicitly labelled estimated.

Schedule lookups and parsed shapes are cached only within each request so a
timetable replacement cannot leave stale global geometry or schedules behind.
Map status, filtering, route results and vehicle profiles share the resulting
presentation. Departure boards, collector matching, bot events and audit
timing-point measurements retain their existing calculations.

Acceptance on the captured 6 September examples: 75, 6a and coach 040 show
waiting at origin; 502 shows a scheduled stopover; A1, m4 and 9 receive route
estimates. These are saved-observation replays, not current duties or a claim
that every unknown vehicle can be resolved. Other timetable/feed discrepancies
remain separate work.
