# Fixed writer audition

`cases.json` contains 30 deliberately selected real collector observations captured
on 25 September 2026: 18 punctual, ten late and two early, including an overnight
event, stand labels, exact matched first/final stops, known destinations and an
out-of-region livery name. This is a test collection, not a representative sample
of bus performance or the live editorial mix.

Vehicle, trip and run identifiers are pseudonyms. The set contains no credentials,
raw fleet file, coordinates or production-host identity. Fleet names are metadata
at capture, not proof of historical allocation. Historical event rows have no
invented journey-position enrichment; exact-stop context comes only from matched
vehicle snapshots and their timetable stops.

Each case records its limitations. Weather is a captured Bath observation replayed
as supplied scenario context, not proof of weather at another bus's location/time.
Traffic is explicitly synthetic. The editorial hook is preselected from the
checked-in fact library at `11422bd`, with its source and qualifications. It does
not exercise live fact selection/cooldowns. These outputs must never be published.

The fixture order is an audition sequence, not chronological history. The writer's
subject cycle and recent-publication memory advance after each simulated accepted
post or deterministic fallback. Rejected drafts do not enter that history.

`baseline.json` is the recorded model experiment with prompts,
mechanical checks, verifier verdicts, usage and provenance. It is an immutable
comparison reference, not approval of its prose. Maintainer review happens on the
comparison page and PR before accepting changes to the bot's voice.

The initial recording used `gemini-3.6-flash`: 74 calls from a 120-call allowance,
30 verified final outputs, eight cases repaired and 83,796 reported total tokens.
All six subjects occur. Replaying the recording reproduced every final post and
its publication history with zero new calls. Passing the verifier is not proof
of good prose; this selected collection cannot estimate the live fallback rate.

Do not silently refresh these cases or rewrite the baseline. A new source snapshot
is a new experiment with a new fixture digest and a separately approved budget.
