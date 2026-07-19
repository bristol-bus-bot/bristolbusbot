# pipeline

Heavy timetable builds run on the Windows workstation. The Pi runs only the
scheduled snapshot, rollup and export/publish jobs under systemd timers.

The supported timetable command is `python deploy/push.py --refresh-timetable`.
It combines three sources because BODS South West GTFS can be lossy:

1. BODS South West GTFS provides the bulk timetable.
2. Operator TransXChange recovers routes omitted by GTFS.
3. TNDS supplies routes absent from BODS altogether.

The job builds and enriches a sibling staging database, imports matching route
shapes, validates SQLite integrity, service-date freshness, expected First
routes and shapes, then performs one atomic promotion. A failed stage leaves
the known-good timetable pathname untouched.

From the repository root:

```powershell
python -m pytest pipeline\tests -q
```

Production refreshes use `python deploy\push.py --refresh-timetable`; it validates locally,
uploads to a fixed staging name, atomically promotes, and checks collector,
site and bot with automatic database rollback. SSH host-key verification is
mandatory. Deploy scheduled job code separately with
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
