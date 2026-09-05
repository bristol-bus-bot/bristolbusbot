# pipeline

Heavy production timetable builds run in GitHub Actions. The Pi schedules the
refresh, independently validates the delivered artifact, and owns guarded live
promotion and rollback. The Windows workstation is the development and
emergency fallback path.

The timetable builder combines three sources because BODS South West GTFS can
be lossy:

1. BODS South West GTFS provides the bulk timetable.
2. Operator TransXChange recovers routes omitted by GTFS.
3. TNDS supplies routes absent from BODS altogether.

The job builds and enriches a sibling staging database, imports matching route
shapes, and materialises the small `stop_routes` lookup used by website search.
That lookup is generated once from the final merged timetable so the Pi never
has to join millions of stop-time rows inside a web request. The job validates
SQLite integrity, service-date freshness, expected First routes, shapes and the
lookup before one atomic promotion. A failed stage leaves the known-good
timetable pathname untouched.

From the repository root:

```powershell
python -m pytest pipeline\tests -q
```

Normal production refreshes use the fixed `timetable-build` workflow and the
Pi's daily shadow-delivery timer. A successful candidate is independently
revalidated, copied to fixed staging, atomically promoted, and checked by the
collector, site, bot, the real stop-search endpoint and public health gates with
automatic database rollback.
The path is proven in production: a production `auto` candidate was accepted on
22 July 2026 after every Pi-side and public functional gate passed. That
commissioning run was manually initiated; the daily timer owns future checks.
`python deploy\push.py --refresh-timetable` remains the attended workstation
fallback. SSH host-key verification is mandatory. Deploy scheduled job code separately with
`python deploy\push.py --component pipeline`; that never replaces the timetable.

`refresh_enrichment.py` audits and refreshes fleet, livery, description,
geography and route-shape inputs. Other scripts implement audit rollups and
exports, fleet refresh, geocoding and boundary generation.

`audit_snapshot.py` writes the private daily `expected_trips` denominator. New
rows retain the planned block, stable route/service IDs, vehicle-journey code,
first-stop identity, final scheduled departure and resolved route-edition date needed for later
missing-trip research. These fields do not change coverage or punctuality
figures. They deliberately use the same timetable vocabulary as the
collector's matching-evidence receipts; observed timing points carry their
separate `exact`/`fuzzy` `match_tier`. The migration is additive, so rows made
before 22 August 2026 remain valid with the new clues blank rather than being
retrospectively guessed.

`audit_rollup.py` also writes two permanent private trip-coverage tables before
the 95-day raw-data prune. `daily_trip_coverage` splits scheduled, observed and
unobserved trips by operator, route, timetable direction and the existing
AM-peak/interpeak/PM-peak/evening bands. Its totals use the same scheduled-trip
membership test as the public route coverage; an observation absent from that
day's snapshot cannot inflate either answer. `daily_trip_coverage_days` stores
the collector evidence needed to decide whether the detailed rows are usable:
successful polls across the full scheduled window, poll continuity, feed match
rate and exact/fuzzy/unknown observed-trip counts. `valid_daily_trip_coverage`
is the safe read view and contains only days that passed those checks.

The same completed-day job writes a separate private duty-gap investigation.
`daily_duty_gap_days` records each rejection stage, timetable-detail coverage
and validity by operator. `daily_duty_gap_candidates` retains only a bounded
review receipt for a missing middle journey when the scheduled block is
non-overlapping, both connections are at most 60 minutes, the surrounding
journeys each have one stable and uniquely identifiable match, and the same
single vehicle appears on both sides. The two valid-only views fail closed with
the existing collector health gate and withhold operators whose duty detail is
less than 95% complete. These are **candidate duty gaps, not cancellations**.
None of these tables is read by `audit_export.py`, the audit website or the
committee-pack generator.

Use `audit_rollup.py --backfill-duty-gaps` only after snapshots genuinely carry
the block and end-time fields. It selects only complete retained dates with raw
observations and never reconstructs older duties from a newer timetable.

`frequency_changes.py` is the separate read-only timetable-change report used
for campaign and committee preparation. By default it compares four complete
weeks with the equivalent block 1, 3, 6 and 12 months earlier:

```sh
python3 frequency_changes.py --audit-db /var/lib/bristolbusbot/collector/audit.db
```

It will not fall back to a public route number when the durable registered
route ID is absent. Standard England and Wales bank holidays are visibly
excluded; a weekday needs a repeated network pattern, and route/direction rows
which vary inside either period are withheld instead of averaged. This catches
many timetable transitions, school-service changes and one-off exception days
without pretending the data reliably names every local school calendar.

For a specifically checked pair of periods, provide complete, equal
Monday-Sunday blocks and record the calendar context used:

```sh
python3 frequency_changes.py \
  --baseline-start 20260907 --baseline-end 20261004 \
  --current-start 20261102 --current-end 20261129 \
  --baseline-context "term time" --current-context "term time"
```

Use `--exclude-date YYYYMMDD=reason` for an exceptional local closure or
one-off national holiday. `--format json` exposes the same checked result for
the committee-pack generator, including exact usable/excluded dates,
unavailable-history reasons, percentage changes and withheld unstable rows.
The unit is scheduled journeys in one representative Monday-Friday week, not
operated journeys or cancellations. Rows made before 22 August 2026 do not
contain trustworthy route identity, so current 3/6/12-month results correctly
say unavailable rather than guessing.

`evidence_pack.py` makes a dated, phone-readable local briefing from complete
calendar months. Choose one area, ward or small route group and give the date
of the committee meeting:

```sh
BBB_AUDIT_DB=/var/lib/bristolbusbot/collector/audit.db \
BBB_AUDIT_SITE_DIR=/var/lib/bristolbusbot/pipeline/audit_site \
python3 evidence_pack.py \
  --area "South Gloucestershire" \
  --committee-date 2026-09-18
```

The command writes `index.html`, `briefing.pdf` and the same versioned summary
as `data.json` under a dated `packs/` address. Dated packs are not overwritten
unless `--replace` is explicitly supplied. The next normal audit publish copies
new packs to GitHub Pages without deleting older cited packs.

The default period is the last three complete calendar months supported by the
database. Percentages are recalculated from summed readings and on-time counts,
not averaged from daily percentages. The command refuses a headline whose
first or last evidence falls more than 14 days inside the selected period; use
a shorter complete window instead of presenting partial history under a longer
date label. Route figures need 200 readings to be
treated as more than indicative. Area/ward route evidence is stored daily in
`daily_geo_route_summary`; after first deployment, run
`audit_rollup.py --backfill-geo-routes` once under the normal audit lock to
materialise as much of the retained 95-day raw history as remains. The pack
labels a partially backfilled route table instead of implying it covers the
whole headline period. It also keeps operator identity beside each route number
and labels a route as partial when its evidence starts or ends more than 14 days
inside the selected period.

The generator withholds the preceding-period percentage-point comparison when
the two windows cross a recorded audit-method change. It also labels both
official targets when a three-month window crosses a financial-year boundary,
rather than pretending one target applied to the whole period.

Registered frequency changes remain absent unless enough post-22-August route
identity exists and the maintainer supplies a checked shared context with
`--frequency-context`. A missing frequency section never blocks a punctuality
pack. Every generated pack still needs a wording check before it is shared.

A day fails closed if the collector did not cover at least 90% of the expected
30-second poll slots, fewer than 95% of its recorded polls succeeded, either
end of the scheduled window is missing by more than five minutes, an internal
successful-poll gap exceeds 15 minutes, or the feed-wide match rate falls below
80%. Invalid rows remain private evidence with explicit reasons; their coverage
is withheld from the public route summary and must never be called a
cancellation. `--backfill-trip-coverage` safely materialises the retained raw
history without rewriting published punctuality summaries.

`fbribuses.json` is a generated runtime cache and is intentionally not stored
in Git. Run `python pipeline/update_fleet_data.py`, or
`python pipeline/refresh_enrichment.py --fix` for the complete enrichment
refresh, to create the local cache. The refresh command distributes it to the
site and bot working directories without committing it.

On the Pi, the audit's authoritative private copy is
`/var/lib/bristolbusbot/enrichment/fbribuses.json`. Pipeline releases validate
that durable file during setup and health checks, but never package or replace
it. The initial copy was bootstrapped atomically from a validated live release
on 4 August 2026 with 2,605 vehicles and 4,386 lookup
entries. The later enrichment automation phase will own candidate generation
and atomic promotion. Legacy site and bot release paths remain read-only
fallbacks for development and recovery, not the production source of truth.

`audit_vehicle_identity.py` is the read-only collision check. It compares the
historical bare-fleet-code lookup with registration-first, operator-scoped
matching across vehicles observed in `live.db` and recent `audit.db` data. It
prints bounded counts/examples, never description text, and opens both databases
read-only. For example on the Pi:

```sh
python3 audit_vehicle_identity.py --max-examples 10
```

`refresh_enrichment.py` now writes schema-2 blurb scope with
`OPERATOR:fleet_code` keys. Its legacy `codes` list deliberately excludes
collisions, so the old generators cannot silently create cross-operator text.

Collector matching behaviour is covered by its tests and package README; audit
definitions and limitations are published with the audit site in
`audit-site/AUDIT_METHODOLOGY.md`.
## Snapshot edition provenance

Daily `expected_trips.timetable_edition` labels follow each trip's own
calendar-start cohort in `route_service_editions`. Multiple cohorts may be
active on one route; they must not all be labelled as the newest edition.
Exception-only calendars or timetables without edition metadata produce a
null label rather than an inferred edition.

This corrects future snapshot metadata only. Existing snapshot rows and
published figures are not automatically rewritten. The label is a calendar
cohort, not a source-file identity or proof that same-time journeys should be
deduplicated.
