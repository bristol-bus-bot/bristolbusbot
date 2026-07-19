# Collector design reference

The shared collector is the only production component that polls the Bus Open
Data Service (BODS). It turns live vehicle and disruption feeds into two SQLite
databases consumed by the website, punctuality audit and Bluesky bot.

For the wider system boundaries, see [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md).
For the published statistical definition, see
[`docs/AUDIT_METHODOLOGY.md`](../AUDIT_METHODOLOGY.md).

## Design principles

1. **One poller.** Downstream components read collector output instead of
   making duplicate BODS requests.
2. **Observed values.** Delays are calculated from reported positions and the
   timetable; they are not predicted, blended or smoothed.
3. **Seconds internally.** Consumers decide how to round for display.
4. **Drop uncertain matches.** A vehicle may appear without a delay when no
   scheduled trip clears the matching gates.
5. **Fail soft.** A failed poll does not erase the last successful state;
   readers apply freshness rules using timestamps.

## Inputs

### Vehicle positions

SIRI-VM supplies the vehicle reference, operator, route, direction, journey
reference, origin time, location, destination and recorded timestamp. The
collector rejects malformed entries, stale re-broadcast positions and points
outside the configured West of England boundary before matching.

The poll interval and freshness thresholds are configuration values. Production
defaults are documented alongside the settings in
`collector/src/collector/config.py`.

### Disruptions

SIRI-SX supplies published situations, validity periods, affected operators,
routes and stops. The national feed is filtered to situations relevant to the
West of England. Updates are keyed by situation number and version; closed
situations are retained temporarily for consistent downstream behaviour.

### Timetable

The read-only timetable database is produced by the validated pipeline from
BODS GTFS, operator TransXChange and the Traveline National Dataset. It contains
journeys, stops, calendar rules, timing-point flags and route geometry.

## Trip matching

Matching is scoped and fail-closed:

1. Try an exact journey reference within the vehicle's operator.
2. Otherwise find candidates using operator, route, direction, service date
   and first-stop departure time.
3. Rank candidates by departure-time difference.
4. Reject a candidate when its route has no stop sufficiently close to the
   reported vehicle position.
5. Return no scheduled match when the remaining evidence is ambiguous.

This avoids collisions where unrelated operators or towns use the same public
route number. The selected trip ID is stored with the vehicle so the website
can display the collector's match rather than deriving a second one.

## Delay measurements

### Live estimate

For a matched vehicle, the collector compares the recorded timestamp with the
scheduled time at the nearest stop on the matched trip. This estimate supports
map status and bot-event selection. Distance and freshness gates prevent a
numerically plausible but geographically implausible result being presented as
a delay.

### Audit reading

For each trip and registered timing point, the audit keeps the poll where the
vehicle passed closest to that point. Only observations within 150 metres enter
the published punctuality figure. There is no interpolation or assumed speed.

The published on-time band is −60 to +359 seconds. Live display categories use
separate product thresholds and must not be confused with the audit definition.

## Corroborated bot events

The collector records early or delayed events only after successive polls
support the same classification. Each event preserves its signed delay,
operator, route, vehicle, match source, confidence and corroboration count. The
bot selects among those events and marks one as consumed; it does not recompute
the delay.

## Outputs

### `live.db`

- `vehicles`: latest accepted position and matched journey information.
- `events`: corroborated observations available to the bot.
- `situations`: current and recently closed SIRI-SX disruptions.
- `poller_status`: latest attempts, successes and failure state for each feed.

### `audit.db`

- closest-approach timing-point observations;
- scheduled-trip snapshots needed for denominators and coverage context;
- daily aggregate tables produced by the rollup jobs.

Both databases use WAL mode. Schema creation and compatible upgrades are
idempotent. Durable files live outside deployed code releases.

## Failure behaviour

- Network errors retain the last successful state and update poller health.
- Unmatched vehicles remain visible without a fabricated delay.
- Invalid or stale positions are counted and excluded.
- Database writes use transactions; readers never consume a half-written poll.
- Health endpoints report feed and database freshness independently.

## Verification

Collector tests cover parsing, time handling, exact and fuzzy matching,
position gates, stale-position rejection, delay calculation, database writes,
secret filtering and SIRI-SX handling:

```powershell
python -m pip install -e "collector[dev]"
python -m pytest collector -q
```

Changes affecting audit comparability also require an entry in the measurement
history in [`docs/AUDIT_METHODOLOGY.md`](../AUDIT_METHODOLOGY.md).
