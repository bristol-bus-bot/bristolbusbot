# Where the timetable doesn't fit: investigation (planning #78)

Status: **investigation**. Nothing here is published. This note says what the
data can support, what it can't, and proposes an approach for review before
anything is built for the public.

## The measure

On one journey, the change in lateness between two timing points next to each
other is the time the bus lost (+) or gained (−) on that stretch. Timetabled
minutes for the stretch are the difference between the two scheduled times, and
real minutes are timetabled minutes plus the change. So every result can be said
in plain words: "the timetable allows 6 minutes, buses usually take 9".

`pipeline/segment_analysis.py` computes it. It's laptop-only and read-only,
running on copies of `audit.db` and the timetable database.

## What the data can support (checked)

- **Everything needed is already recorded.** `timepoint_observations` has, for
  each journey and timing point, the scheduled local time and the lateness of
  the closest approach within 150 m. Raw rows are kept for 95 days.
- **The timetable is needed as well.** The audit only stores readings, so on
  its own it can't tell "next timing point" from "a timing point was missed in
  between". The script takes each journey's timing points from the timetable's
  `stop_times.timepoint` and only pairs readings at adjacent timing points.
  It counts and reports skipped pairs. Direction also comes from the timetable.
- **Sample sizes look workable for busy routes, thin for the rest.** Over the
  28 days to 24 October (public audit data):
  - First had 100 routes with journey counts. The median route had **45
    observed journeys per weekday**. The busiest (A1, U1, 1, 4, 41, m1) had
    180–250.
  - The median observed journey has readings at only **about 4 timing
    points**, so about 3 measurable stretches per journey at best, fewer
    where a reading is missing.
  - Rough estimate for weekday 07:00–10:00, per direction and stretch, over 20
    weekdays (assuming about 20% of journeys run in that band and about 60% of
    adjacent pairs are both read): **about 66 of 100 First routes would reach
    30 journeys**. About 29 routes run fewer than 17 observed journeys a
    weekday and won't reach 30 in any band in four weeks. Weekend bands are
    thinner still. These are estimates, not measurements. The first real run
    replaces them.
- **Journey ids change when the timetable is rebuilt.** In the first run,
  only journeys from 23 October onwards (after the 22 October rebuild), plus
  about a third of Monday–Thursday journeys, were still in the timetable.
  Fridays and weekends before then were almost all missing. So for older
  journeys the script judges each stretch by stop code. It accepts a pair only
  if every current journey on that route that serves both timing points has
  them next to each other, in one direction. Pairs it can't confirm are
  counted and left out.
- **Some readings are impossible.** A pair that implies the bus arrived
  before it left (negative running time) comes from a reading on the wrong
  visit to a stop or during a layover. These are counted and left out.

## First run: 27 September – 24 October 2026

Laptop copies taken from the Pi on 25 October: the last four weeks of
`timepoint_observations`, plus the timetable's timing points, stops and routes.

- 713,611 readings in 169,286 journeys gave **524,595 measurable stretches**:
  181,032 matched by journey and 344,134 confirmed by stop code. Left out:
  3,775 pairs that skipped a missing timing point, 15,384 older pairs the
  current timetable couldn't confirm, and 571 with negative running time.
- **4,702 groups** reach 30 journeys, covering 129 of 179 routes (85 of 102
  First routes). Weekday 07:00–10:00 has qualifying stretches on 102 routes;
  weekend 07:00–10:00 on only 32. So the estimate above was about right for
  weekdays, and weekends need a longer window.
- **More stretches run slack than tight.** 309 groups lose more than 2
  minutes at the median, and 1,069 gain more than 1 minute. The median group
  is 6 seconds early. Gains are inflated by recovery time built in before
  timing points (see below), so this doesn't yet mean timetables are
  generally too generous.
- **Examples for review (not checked against local knowledge yet):**
  - Route 1, Temple Meads → The Centre: 7–8 minutes timetabled, typically
    14. It loses more than 2 minutes on 96–99% of weekday journeys at every
    time of day (434 journeys interpeak). Plausibly real: Victoria Street,
    Redcliffe and dwell at the station.
  - Route 13, Southmead Hospital → Charlton Road: 9 timetabled, 16 typical,
    weekday interpeak.
  - Route U3, Lyncombe Hill → St James Cemetery (Bath): 5 timetabled, 12
    typical, weekday pm peak, 99% lose more than 2 minutes.
  - Route X1, Interchange → Worle Interchange: 18–19 timetabled, 23–24
    typical in both weekday peaks.
  - Slack: route 74, Willow Brook Centre → Great Park Road. 17–19 timetabled,
    9–11 typical, at every time and on both day types. That's more likely a
    timing-point or layover quirk, or the "Stop closure: Willow Brook"
    notice in the disruptions feed (planning #76), than a real 8-minute
    cushion. X1/X5 Worle Interchange → Interchange and route 44 Bedminster
    Parade → The Centre look like recovery time before a terminus.
  - Artefact: route 228, Lambridge → Bailbrook House, "loses" 37 minutes
    on a 2-minute stretch. The next stretch "gains" it back, so it's a
    layover or a stop read on the wrong visit, not traffic. Stretches like
    this should be removed in review rather than by a rule.

## What it can't support (limits to state publicly)

- **Per-journey figures are noise.** Positions arrive about every 30 seconds,
  so each reading can be out by up to about half a minute and a stretch by up
  to a minute. Only medians over many journeys mean anything. The 2-minute
  "lost" threshold is deliberately above that noise.
- **The first stretch of every route can't be measured.** Origin readings are
  excluded, because a bus waiting on its stand looks the same as one leaving
  early.
- **Holding at timing points is invisible.** A bus that's early and waits
  shows up as early at one point and on time at the next. The script can show
  "usually arrives early, then loses it" but can't see the wait itself.
- **One-off causes look like timetable problems.** Roadworks or an accident
  can make a stretch look tight. Medians over 4+ weeks, split by band and day
  type, separate patterns from one-offs. Overlaying roadworks (planning #76 research)
  is the later fix.
- **Recovery skews things.** A bus that's already late may dwell less and
  "gain" on the next stretch. A median gain after a known pinch point may be
  this rather than slack.
- **Timetable changes split stretches.** A new timetable edition can change
  timing points or minutes. Groups are keyed by stop codes and keep the
  median timetabled minutes, but a window that spans a change should be split
  at the change before anything is published.

## Proposed approach

1. ~~**First real run (laptop).**~~ Done on 25 October (above). The full CSV of
   4,702 groups stays on the laptop and isn't committed.
2. **Review (Tom).** Look at the worst 20 "too tight" and worst 20 "too
   slack" stretches against local knowledge. Does a known pinch point lose
   minutes at 8am? Do a few by hand from raw readings to check the arithmetic.
3. **Decide whether and how to publish (Tom).** The research comment's
   suggestion, in order:
   - the audit route page: a route "ribbon" coloured by time lost or gained,
     a stretch × band grid and a "timetable allows / buses take" table for the
     worst five stretches, in per-route files like the hourly ones (planning #77);
   - the bus profile Record tab, showing "how this route usually runs, all
     buses", never a per-bus stretch analysis;
   - Journey tab hints and live-map colouring last.
4. **Then automate.** Once the method is trusted, compute it nightly in the
   rollup like the hourly table. It's a single pass over the day's raw rows
   plus a timetable lookup, small enough for the Pi.

## Decisions needed

- Is a 4-week window with a 30-journey minimum the right bar? The first run
  says it works for weekdays but not weekends (32 routes at weekend
  07:00–10:00), so weekends probably need 8 weeks.
- Should stretches into a terminus or known layover point be left out, or
  labelled as "recovery time"? They make up most of the biggest "slack"
  results.
- Band or hour? Bands (early, 07–10, 10–16, 16–19, evening) keep groups big
  enough. Hours would match #77 but would leave out most stretches.
- Publish "too slack" as well as "too tight"? It's fair and answers the
  driver's second point, but slack is harder to see (holding is invisible),
  so the wording needs care.

The methodology now says plainly that the figures measure the service against
its timetable, not individual drivers.

## Running it

```
python pipeline/segment_analysis.py audit-copy.db timetable-copy.db --from 20260927 --to 20261024 --out segments.csv
```
