# Travel notices

Stop and route selections show source-linked notices from the collector's existing
SIRI-SX feed. The initial publishing scope is the `WestofEngland` participant.
Stop notices require an exact timetable stop ID match; route notices require both
operator and public line name. A notice does not establish an individual journey
cancellation, complete network coverage, or an explanation for a bus's lateness.

The collector preserves every raw `ValidityPeriod` in the additive
`affected_json.validity_periods` field. Single-period legacy columns are retained;
multiple periods are never flattened into one continuous closure. No database
schema version bump or rebuild is required. Successful polls backfill existing
versions. Situation keys include participant and number; the first successful
poll withdraws legacy unscoped keys. XML bytes are parsed directly to preserve
the source encoding.

`/api/notices?stop=...` or `?operator=...&line=...` returns current, upcoming
(within 14 days), or date-uncertain notices. Times require timezone-aware,
ordered start/end pairs. Unknown period qualifiers, incomplete dates and detected
explicit clock-range contradictions produce an uncertainty warning rather than
an active-closure assertion. Prose checking is conservative and does not prove
the source is consistent. Date-uncertain notices older than 30 days are omitted.
Dates render in Europe/London, including daylight saving.

The feed must have succeeded within 15 minutes. Expired periods and explicitly
closed or withdrawn notices disappear without an all-clear message. Missing
legacy period metadata is not presented as reliable. The browser refreshes notices
at most once per minute while a stop/route is selected, using the existing UI
refresh cycle; it never polls BODS directly. Selection changes discard late
responses. Source text is rendered as text, and links require HTTPS.

## Seeing a notice before tapping

`/api/notices/stops` returns `{stop_code: status}` for every stop with a notice
the stop panel would show, using the same rules, and nothing while the feed is
out of date. The map refreshes it once a minute. A stop with a current or
date-uncertain notice is drawn larger, in amber, with a "!" mark; an upcoming
notice gets an amber ring. The popup says which. Selecting the stop puts a
one-line banner above the stop sign ("Travel notice affects this stop", "...:
check dates" or "Upcoming travel notice...") that opens the first notice card,
and on phones expands the sheet. Route selections show the banner above their
cards. Only stops listed in the notice's affected stops are badged: a notice that
names a stop only in its text is never shown as affecting it.

`site/scripts/notice_gaps.py` measures, on a laptop copy of `live.db`, how many
disruptions each publisher sent, how many list affected stops, how many mention a
stop only in their text (listed for the maintainer) and how many are skipped for
missing or uncertain dates.

Notice updates replace the stored version; this is not a complete disruption
history or a measure of publisher responsiveness. A missing notice does not mean
normal service. Notifications and social posting are outside this feature.
