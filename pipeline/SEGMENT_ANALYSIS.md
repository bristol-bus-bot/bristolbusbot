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
- **Review of the results (done, 25 October).** Each stretch went through four checks:
  1. **Reading errors removed (26 stretches).** A big loss that is won back on
     the very next stretch (route 228 Lambridge → Bailbrook House "loses" 37
     minutes) is a bad reading, not traffic.
  2. **Steady every week (95 removed).** A weekday result counts only if at
     least 3 of the 4 weeks, each with 5+ journeys, point the same way. That
     filters out one-off closures and roadworks.
  3. **Recovery time is not slack (115 stretches).** Slack on the last
     stretch into a terminus is time deliberately built in for the bus to get
     back on schedule. It's listed separately, not as a timetable fault.
  4. **Waiting is not traffic (18 stretches).** Readings are taken when the
     bus first reaches a stop, but timetables give departure times. So if
     buses usually sit at a stop because they're early, the wait shows up as
     "lost" on the next stretch. Checked by hand: X1 buses at Weston
     Interchange are typically 3.5 minutes early there and then wait. These
     stretches are labelled "buses wait here", which is the driver's second
     point.

  **What's left:** 106 stretches where the timetable is steadily too tight,
  and 192 where it's steadily too slack (not counting recovery time).

  Most clearly too tight (weekday, worst time of day, typical minutes vs
  timetable):

  | Route | Stretch | Timetable | Typical | Lose 2+ min | When |
  |---|---|---|---|---|---|
  | 1 | Temple Meads → The Centre | 7 | 14 | 100% | all day |
  | 13 | Southmead Hospital → Charlton Road | 9 | 16 | 96% | 7am–7pm |
  | 20 | Cranhill Road → Broad Quay (Bath) | 9 | 15 | 96% | 7am–7pm |
  | U3 | Twerton Mill → Green Park (Bath) | 9 | 14 | 87% | all day |
  | X6 | Triangle → Six Ways | 10 | 15 | 93% | 7am–7pm |
  | 9 | The Haymarket → Roman Way | 13 | 17.5 | 80% | all day |
  | Y2 | St Saviour's Church → The Centre | 20 | 25 | 90% | most of the day |
  | 2 | The Centre → Black Boy Hill | 18 | 22 | 72% | most of the day |
  | 77 | Lockleaze Road → The Centre | 22 | 26 | 84% | 7am–7pm |
  | 4 | Royal United Hospital → Bus Station (Bath) | 16 | 21 | 71% | 7–10am |

  Route 1 is a clean example: buses are on time at Temple Meads (median
  12 seconds early) and typically 5 minutes late by The Centre, at every time
  of day.

  Most clearly too slack (buses run early and have to wait):

  | Route | Stretch | Timetable | Typical | 1+ min early | When |
  |---|---|---|---|---|---|
  | X1 | Worle Interchange → Interchange | 24 | 18 | 94% | all day |
  | 2 / 2a | Temple Meads → Sturminster Road | 24 | 16 | 89% | 4–7pm |
  | m1 | Frenchay Campus → Broadmead | 22 | 17 | 88% | 7am–7pm |
  | 16 | Granville Street → Rupert Street | 25 | 19 | 87% | 7am–7pm |
  | 18 | Portland Street → Cecil Road | 14 | 9 | 96% | 7am–7pm |
  | 45 | Croydon Street → The Centre | 20 | 16 | 86% | all day |

  Bus stops buses usually wait at, which inflates the next stretch: X1 and
  X1a at Weston Interchange, 1 at Interchange, 47x at Ettricke Drive, U3 at
  Lyncombe Hill, 44 and 2a at The Centre, 16 at Nelson Street.

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
2. ~~**Review.**~~ Done (above). The checks are repeatable and should become part
   of the nightly job: reading errors, week-to-week steadiness, recovery time,
   and waiting points.
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
