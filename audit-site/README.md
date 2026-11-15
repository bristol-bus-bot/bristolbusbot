# WECA bus punctuality audit

An independent, non-commercial measurement of bus punctuality across the West
of England, built from operators' public open data and compared with the dated
annual area-wide target in the Enhanced Partnership Scheme. The published
2026-27 target is 87%; the separate long-term goal is 95% by 2030.

**Live audit:** https://bristol-bus-bot.github.io/weca-bus-audit/

The static interface is maintained in the `audit-site/` directory of the
[BristolBusBot source repository](https://github.com/bristol-bus-bot/bristolbusbot).
Collection, matching and aggregation code is in `collector/` and `pipeline/`.
The publication job copies the interface and generated aggregate data to the
separate `weca-bus-audit` GitHub Pages repository.

The full definitions, data sources and limitations are in the
[published methodology](https://github.com/bristol-bus-bot/weca-bus-audit/blob/main/AUDIT_METHODOLOGY.md).
The audit is independent and may differ from figures produced with different
sampling or matching methods.

`docs/packs/` keeps a page from a 2026 test of printable area summaries. It
was a test only and isn't part of the audit.

Contains public sector information licensed under the Open Government Licence
v3.0. The Department for Transport and its agencies accept no responsibility
for the accuracy, timeliness or completeness of the data. Project code is
licensed under AGPL-3.0-only except where otherwise noted.

Not affiliated with, endorsed by, or funded by any bus operator, the West of
England Combined Authority, or any authority.

## How this works

The page is a visual scorecard. The explanations formerly shown on the page
are collected below, with links to the full method where it already covers
them. Search for a route or place, select an hour or place for its readings,
and open a route for its timetable, journey and possible-reason panels.

### The point

The audit compares independent measurements with WECA's dated annual target
and longer-term goal. Operators publish their live vehicle data; the audit
uses it to build a route-level public scorecard. See
[What this is not](AUDIT_METHODOLOGY.md#what-this-is-not) for the purpose,
the lack of an accessible ongoing route-level public performance record,
operators' publication duties and why these are independent estimates rather
than official operator or regulator figures. Neither the page nor its
possible reasons measures individual drivers: it measures the timetable and
the service.

### The headline and its markers

The large figure is the share of **readings**, rounded to “in 10”, with the
precise percentage beside it. The selected operator and period appear above
it. [Where delay is measured](AUDIT_METHODOLOGY.md#where-delay-is-measured)
explains the 150-metre timing-point gate: we measure the buses we can observe
each day against the published timetable, not every bus that actually ran.

The bright tick marks the annual area-wide target, not an operator-specific
target. Green means at or above it; amber is within 25 percentage points;
red is below that. The gap on the bar shows the shortfall; its numerical
value remains in the Evidence expander. The source is the
[Enhanced Partnership Scheme V7.02, July 2025, Appendix 5, Table 9](https://www.westofengland-ca.gov.uk/wp-content/uploads/2025/07/West-of-England-EP-Scheme-V7.0-July-2025.pdf).
The separate long-term goal is **95% by 2030**. The dated target table and
source comparison are in the
[on-time definition](AUDIT_METHODOLOGY.md#on-time-definition).
When the schedule runs out, no current target is invented: the tick names
the last published percentage and financial year for reference. Its label
says “last published”; the accessible chart description also names the year
with no published target. The reference still sets the chart colours.

The fainter **strict** tick counts early readings at the final timing point
as early instead of on time. The main figure treats them as on time, as the
official final-destination rule allows. The strict percentage is unchanged;
the number of affected readings remains in Evidence and in the chart's
accessible description and hover text. See the
[on-time definition](AUDIT_METHODOLOGY.md#on-time-definition) for the rule,
its source and the unchanged treatment of late readings.

### Reading the split bars

Each bar shows the share of eligible readings. Wide segments also display
their percentage. Hover over a segment or use a screen reader for its
definition; all percentages remain in the legend, including narrow segments.

The four short names replace these former visible definitions:

| Label | Meaning |
|---|---|
| Early | More than a minute ahead of the timetable; the bus may have arrived early and waited. |
| On time | Up to a minute early or under 6 minutes late, with the final-stop exception above. |
| Late | 6 to 20 minutes; where the separate very-late count is unavailable, 6 minutes or more. |
| Very late | 20 minutes or more. |

For precise boundaries and all six bands, see
[The six delay bands](AUDIT_METHODOLOGY.md#the-six-delay-bands). The expanded
bar retains all six percentages and definitions. Its short labels replace
the repeated descriptions “more than 5 minutes ahead of the timetable”,
“1 to 5 minutes ahead”, “up to a minute early or under 6 minutes late”,
“6 to 10 minutes late”, “10 to 20 minutes late” and “20 minutes or more late”.
A reading is neither an arrival nor a departure; the
[honest limitations](AUDIT_METHODOLOGY.md#honest-limitations) explain why.

### Evidence and missing readings

Faded routes, hours and wards have too few readings to rely on. Low-sample
routes, patchier feeds from smaller operators and partial collection days
are not reliable on their own. The Evidence expander retains sample status,
journey and service-day counts, sampling range, operator shares, excluded
dates, routes with withheld coverage, and frequency-group figures.

[Journey sample qualification](AUDIT_METHODOLOGY.md#journey-sample-qualification-export-method-3)
explains unavailable and indicative results, missing historical support,
unverified scheduled-service coverage and inconsistent journey order.
The range assumes independent journeys and covers sampling and known
journey-order sensitivity; it does not cover missing-feed bias or other
assignment errors. Operator shares describe the selected day's or period's
eligible readings. Places and operators without observations are not
represented. This is timetable adherence, not excess waiting time or a
passenger survey; see also
[Frequent services](AUDIT_METHODOLOGY.md#frequent-services).

[Duplicate timetable entries](AUDIT_METHODOLOGY.md#routes-with-duplicate-timetable-entries)
explains why a route's journey coverage may be withheld while its punctuality
still counts: identical scheduled journeys make the denominator uncertain.
Frequency-group route counts show how many routes support a figure; the
others have too few readings. Unavailable figures remain marked as such.

### Hours and journeys

The low/high labels retain the old time-of-day summary's hours and “in 10”
figures. They compare only well-measured hours. “Similar throughout” means
the best and worst round to the same “in 10” result. If there are too few
readings, no hourly comparison is made. Select an hour for its full figures.
[Time of day](AUDIT_METHODOLOGY.md#time-of-day) explains the scheduled-hour
basis, weekday/weekend split, date window, service-day count and faded hours.

The three journey cards remain separate: seen, very likely did not run,
and not seen. “Very likely” uses the same bus on the journeys immediately
before and after, with this journey unseen. “Not seen” could mean a
cancellation or missing tracking; we cannot tell which. These are not
combined into a cancellation count. The figures pool weekdays and weekends,
using only days when collection worked properly. The “most unseen” label
retains the time band and number in 100. See
[Did the buses turn up?](AUDIT_METHODOLOGY.md#did-the-buses-turn-up) and
[Coverage](AUDIT_METHODOLOGY.md#coverage).

### Timetable shortfalls and possible reasons

Stretches compare scheduled and observed typical minutes. The small label
retains the time band, journey count and share losing at least two minutes.
These are persistent patterns between timing points over 28 days, present
in at least three of four weeks. Spare time can produce early running and
waiting. See [Where the timetable doesn't fit](AUDIT_METHODOLOGY.md#where-the-timetable-doesnt-fit)
for definitions, exclusions and why this is a planning issue, not a driver
score: drivers can only recover time by driving faster or pulling away before
people have sat down.

**Possible reasons are suggestions, not facts.** Each keeps its evidence.
[Likely reasons](AUDIT_METHODOLOGY.md#likely-reasons) describes spare time
before a stop paired with too little after it, shorter timetabled journeys
after 10am without faster observed journeys, and trips starting progressively
later. Moving spare minutes from before a stop to after it may fit the
observations better; progressive lateness may suggest too little recovery
time at the end of a route. These are possibilities, not established causes.
The other suggestions are a persistently tight stretch, a much worse hour,
unseen journeys, or possible waiting at a stop. A worse hour identifies when
the pattern is strongest, not its cause. Early arrival and waiting can make
the next stretch look slow on paper. When there is no clear pattern, the
page suggests no reason; late running may be spread across the day and route.

The route's typical and average lateness figures use minutes and seconds,
with **+** for late and **−** for early. The route link opens its live-map
page. Route descriptions are explained in
[Where each route goes](AUDIT_METHODOLOGY.md#where-each-route-goes).

### Places and vehicles

A ward can look poor because a busy route through it struggles. Select it
to see the routes behind its figure. Larger ward dots mean more readings;
the default list shows the least punctual well-measured wards, with search
for others. See [Geography](AUDIT_METHODOLOGY.md#geography-area-and-ward).
“Unmapped” counts readings at stops we cannot place in a West of England
council area, mostly just beyond its edge; both the count and percentage
remain on the page.

Vehicles are assigned routes by the operator. Their punctuality says more
about their work than the vehicles themselves; see
[Fleet](AUDIT_METHODOLOGY.md#fleet-model-and-electric).
The electric share gives electric vehicles out of all observed vehicles.
The accompanying on-time comparison shows electric and other vehicles'
readings, including equal figures. Fuel legends retain vehicle counts and
percentages. Model names link to Wikipedia.

### Historical data, full method and data

Some days have incomplete or contradictory evidence and are excluded.
Method and timetable changes limit comparisons across periods. See the
[dated change record](AUDIT_METHODOLOGY.md#changes-to-the-measurement).
The [full methodology](AUDIT_METHODOLOGY.md) covers data sources (including
TNDS routes not published to BODS), geography, fleet, frequent services and
limitations. [Day-by-day history](https://bristol-bus-bot.github.io/weca-bus-audit/audit_data.json)
remains downloadable. Code is in the source repository linked above; the
published repository holds the interface and aggregate data.
