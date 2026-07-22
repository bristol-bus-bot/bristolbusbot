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
site and bot working directories; deployment includes those local copies
without committing them.

Known source-data hazards and matching rules are documented in
`docs/plans/COLLECTOR_SPEC.md`; audit definitions and limitations are in
`docs/AUDIT_METHODOLOGY.md`.
