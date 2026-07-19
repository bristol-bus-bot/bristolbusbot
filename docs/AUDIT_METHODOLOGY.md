# WECA bus punctuality audit: methodology

An independent measurement of bus punctuality across the West of England,
built only from operators' own public open data and compared against the
published 95% punctuality target. It covers the local registered bus operators
in the WECA area (First Bristol, Stagecoach West, The Big Lemon, Abus, CT Coaches
and others), not long-distance coaches or ferries. This page sets out exactly how
the figures are produced and where they can be wrong.

## Data sources

Real-time positions come from the Department for Transport's Bus Open Data
Service (BODS) SIRI-VM feed, polled continuously and filtered to the WECA
boundary by point-in-polygon, not just a bounding box.

The timetable to measure against is built from three layered open sources,
because no single one is complete:

1. **BODS South West GTFS** for the bulk of all WECA operators.
2. **Operators' own BODS TransXChange**, to recover routes the GTFS conversion
   silently drops (the BODS GTFS is a lossy conversion that loses some complex
   routes day to day).
3. **The Traveline National Dataset (TNDS)** for routes operators run but do not
   publish to BODS at all (for example several First Bristol services). This is
   the same source bustimes.org falls back to.

Nothing is bought, scraped from behind a login, or supplied privately. Anyone
with a free BODS key and free TNDS access can pull the same data.

## Matching a vehicle to a scheduled trip

Operators' SIRI feeds label each vehicle with a DatedVehicleJourneyRef that is
usually the scheduled start time (HHMM), not a unique journey ID. So a live bus
is matched to a timetabled trip by: operator code, route, direction, first-stop
departure time (within 10 minutes), and the calendar day's service pattern.
Scoping the match to the vehicle's own operator resolves cases where different
operators or different parts of the region share a route number. This is a fuzzy
match, not a guaranteed one; where a confident match cannot be made, the reading
is dropped, not guessed.

## Where delay is measured

Delay is recorded at timing points, the registered points the punctuality
standard is based on, not at every stop. For each timing point on each day we
keep the single reading where the bus passed physically closest to it, by GPS
distance. Only readings within 150 metres of the timing point count towards the
published figure; readings further out are stored but excluded, and we report how
many were excluded and the median distance of those kept. There is no
interpolation and no assumed speed: every figure is a real recorded position.

## On-time definition

A departure counts as on time if it is between 1 minute early and 5 minutes 59
seconds late (delay between -60 and +359 seconds). This is the DfT statistical
"on time" band, chosen because it matches the convention behind the official
published figures. The target is 95% on time, the figure in the West of England
Bus Service Improvement Plan and the Traffic Commissioner's window of tolerance.

## Geography (area and ward)

Each timing-point reading is tied to its stop's unitary authority (Bristol, Bath
& North East Somerset, South Gloucestershire, North Somerset) and electoral ward
using a stop-to-locality lookup, and rolled up so on-time performance can be seen
by area and by ward. This shows where service is better or worse, but a ward's
figure only reflects the stops we observed there, so sparsely-measured wards are
treated as indicative.

## Fleet (model and electric)

Each reading carries the vehicle reference, which is matched to fleet data to get
the model and whether it is electric. This drives the electric-vs-diesel split
and the per-model figures, including which service numbers each model runs.
Vehicles are assigned to routes operationally and change day to day, so by-model
punctuality is indicative of how a vehicle type performs on the work it happened
to do, not a verdict on the vehicle itself.

## Frequent services

A route is flagged frequent if it runs 6 or more buses in its busiest daytime
hour, the DfT high-frequency threshold. For frequent services the official
standard measures excess waiting time rather than timetable punctuality, so the
on-time figure shown for a frequent route is informative but not the basis on
which such services are formally judged.

## Coverage

Coverage is the share of scheduled trips we actually observed running on the
feed. It is a rough indicator only. A trip we did not see is not proof of a
cancellation: it may be a vehicle that was not broadcasting, a GPS dropout, or a
match we could not make. Coverage is reported as context, never as a count of
cancelled services.

## Honest limitations

- A bus missing from the feed is not the same as a bus that did not run.
- Positions are sampled roughly every 30 seconds, so the closest-approach reading
  is within a few seconds of the truth, not exact.
- The match is fuzzy, so some trips are unmatched and excluded.
- We measure at timing points only, which is the official basis, not at every stop.
- Operators differ in data quality: First's real-time feed is rich, while some
  smaller operators publish patchier data and are measured with less certainty.
- Early days carry small samples, so per-route, per-area and per-operator figures
  can swing until enough days accumulate. Low-sample figures are flagged.

## What this is not

Operators publish the timetable, vehicle-location and fares data they are legally
required to publish under the Bus Services Act 2017 and the 2020 Open Data
Regulations. Performance figures are not on that list, so this audit does not
claim any operator is breaking a publication duty. The point is narrower and
factual: a public 95% target exists, the data to measure against it is free and
open, but no accessible, ongoing, route-level public record of actual performance
exists. This fills that gap. These are independent estimates built to the
official definition, not the official figures, and will differ from any operator
or regulator measurement that uses different sampling.

## Changes to the measurement

Changes that affect comparability of the published figures are recorded
here.

**13 July 2026 — collector replaced.** The process that matches live buses
to timetabled trips was rewritten. Three behavioural changes affect the
figures: a match is now rejected unless the candidate schedule has a stop
within 3 km of the vehicle's reported position; where several schedules
share a route number, the one with the closest departure time is chosen
rather than an arbitrary pick; and stale re-broadcast vehicle positions,
which BODS emits for parked vehicles, are discarded rather than recorded.
Together these raised measured on-time performance by roughly 1 to 1.5
percentage points on weekdays and increased the number of matched trips by
around 15%. Figures before this date came from the old process and are not
directly comparable.

**14 July 2026 — timing points restored on TransXChange-sourced routes.**
Routes that enter the timetable via TransXChange rather than GTFS (42, 43,
44, 45, 70, 74, 373, 376x, AZ1, AZ2, N43, X12 and the SB school services)
carried no timing-point flags, because the conversion recognised only the
long-form TimingStatus value and not the three-letter code First's files
use. As delay is only recorded at timing points, these routes never
produced a published reading despite being tracked live. Fixed on 14 July;
they accumulate data from that date, which is why their history starts
there.

## Sources

- Bus open data policy, GOV.UK: https://www.gov.uk/government/collections/bus-open-data-service
- BODS operator requirements: https://publish.bus-data.dft.gov.uk/guidance/operator-requirements/
- Traveline National Dataset (TNDS): https://www.travelinedata.org.uk/traveline-open-data/traveline-national-dataset/
- Senior Traffic Commissioner, Statutory Document No. 14: https://www.gov.uk/government/publications/traffic-commissioners-local-bus-services-in-england-outside-london-and-wales-november-2018
- DfT, Proportion of bus services running on time: https://www.gov.uk/government/publications/proportion-of-bus-services-running-on-time
- West of England Bus Service Improvement Plan (2024): https://www.westofengland-ca.gov.uk/wp-content/uploads/2024/07/3882.Bus-Service-Plan-2024_v2-1.pdf
