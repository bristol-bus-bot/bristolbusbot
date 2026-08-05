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
