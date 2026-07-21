# Timetable build migration: GitHub builds, the Pi promotes

Status: approved architecture; implementation in progress.

This is the architecture decision for removing the Windows workstation from
production timetable refreshes. The companion execution plan is
`TIMETABLE_BUILD_PI_EXECUTION.md`; the wider data-estate plan is
`DATA_REFRESH_AUTOMATION.md`.

## Decision

Use GitHub Actions as the primary compute plane and the Raspberry Pi as the
production control and safety plane.

```text
Pi detects that a refresh is due
  -> Pi triggers the fixed workflow on the repository default branch
  -> GitHub downloads sources, builds, validates, and publishes a candidate
  -> Pi downloads that candidate and its manifest
  -> Pi independently validates and compares it with the live database
  -> Pi atomically promotes, restarts consumers, and health-checks
  -> success is accepted; failure rolls back to the previous database
```

The laptop remains a development machine and an attended emergency fallback.
A resource-capped full build on the Pi remains an optional disaster fallback,
not a prerequisite for automation.

## Why this is the right split

The goal is to remove the laptop dependency, not to make the Pi perform every
calculation. GitHub runners have ample memory and CPU for a short weekly batch
job. The Pi is already the authority for the live file, consumer restarts,
health checks, rollback, monitoring, and backups.

The production Pi is a Raspberry Pi 4 Model B with about 906 MiB usable RAM,
zram swap, a 250 GB main SSD, and a separate USB backup drive. A full build can
probably be made to fit, but it needlessly competes with the collector and live
services. Downloading and validating a roughly 200 MiB finished database is a
much smaller and safer workload.

## Measured evidence

The complete cached-input pipeline was benchmarked on the Windows workstation.
The measurements below are evidence, not estimates.

| Build | Wall time | Peak RSS | Finished database | Result |
|---|---:|---:|---:|---|
| Current implementation | about 155 s | about 379 MiB | 404.63 MiB | validation passed |
| Experimental optimized implementation | about 155 s | 192.9 MiB | 196.47 MiB | validation passed |
| Optimized source implementation, cached inputs | 101 s | not remeasured in this run | 194.85 MiB | strengthened validation passed |

The optimized experiment produced equal content hashes for all seven GTFS
tables and `route_shapes`. It removed only unused storage and transient memory:

- stream GTFS rows rather than materialising whole CSV files;
- use a small SQLite cache and file-backed temporary storage;
- keep raw shape points in a temporary SQLite table;
- store only derived `route_shapes`, because no production consumer reads the
  raw `shapes` table;
- remove two redundant left-prefix indexes;
- stream network downloads and ZIP members rather than holding whole bodies;
- finalize with safe static-database pragmas and SQLite analysis.

These changes still matter with GitHub as the builder: the delivery is smaller,
backups are smaller, the work is easier to reproduce, and a future Pi fallback
is more plausible.

The first source implementation run on 21 July 2026 produced 250 routes, 35,814
trips, 6,224 stops, 1,183,343 stop times, and 376 route shapes. It had no raw
`shapes` table, no duplicate `(trip_id, stop_sequence)` groups, used `DELETE`
journal mode, and passed the strengthened production validator.

## Non-negotiable contracts

### Candidate generator

Every build stage must:

- take explicit source and output paths;
- write only inside a disposable build directory;
- never open a production path for writing;
- fail when any required source is missing, empty, corrupt, or incomplete;
- never publish a partially enriched timetable;
- leave a structured stage result with timing and named failure information.

Unsafe SQLite settings such as `journal_mode=OFF` and `synchronous=OFF` are
allowed only for a disposable candidate. Finalization must restore the static
database contract expected by production, including `journal_mode=DELETE`.

### Artifact

The workflow publishes one short-lived delivery artifact containing only:

- `timetable.db`;
- `manifest.json`;
- the required Open Government Licence attribution.

It must not contain downloaded source archives, secrets, fleet information from
bustimes.org, logs containing credentials, or unrelated enrichment data.

The manifest records at least:

- manifest schema version;
- SHA-256 and byte size of `timetable.db`;
- builder commit and GitHub workflow run ID;
- build start and finish time;
- source identifiers, timestamps, sizes, and hashes where available;
- table row counts;
- maximum service date;
- expected First-route result;
- timetable route/direction keys and route-shape keys;
- validator version and result.

Artifact retention is seven days. It is a delivery parcel, not a backup.

### Pi acceptance

The Pi accepts a candidate only when all of the following are true:

- it came from the exact timetable workflow;
- the workflow run succeeded on the repository default branch;
- the run event and commit are allowed;
- the manifest version is supported;
- the artifact is fresh, within a size ceiling, and has not already been used;
- the artifact contains exactly the allowed regular files and extracts safely;
- its hashes match;
- independent Pi-side validation passes;
- comparisons with the current database do not show an unexplained collapse.

The Pi never treats a GitHub success badge as permission to promote.

### Promotion transaction

A Pi-side privileged helper owns the complete transaction:

```text
take maintenance lock
  -> final validation
  -> retain timetable.db.previous
  -> atomic replacement
  -> restart collector, site, and bot
  -> health checks
  -> accept or roll back
  -> release lock
```

The downloader and parser run unprivileged. The promoter does not download or
parse untrusted source data. The same maintenance lock is respected by code
deployment, timetable promotion, backup, and any other conflicting maintenance
job; timer spacing is convenience, while the lock provides correctness.

## Automation trigger

The Pi remains the scheduler of record. A daily read-only freshness check
dispatches the GitHub workflow when the timetable enters its refresh window and
no suitable build is already running or recently completed. The credential is
a fine-grained token restricted to this repository and GitHub Actions write
permission; it cannot push code. Its expiry is monitored in the existing
health digest.

The workflow also supports attended manual dispatch. A GitHub `schedule` may be
kept as a secondary trigger, but it is not the only trigger: public-repository
schedules can be delayed, dropped under load, or disabled after prolonged
repository inactivity.

There is no inbound access to the Pi and no Pi SSH key in GitHub.

## Validation gates

The current gates remain and are strengthened. A candidate must pass:

- SQLite `integrity_check`;
- static `DELETE` journal mode;
- current and sufficiently long service window;
- required First Bristol routes;
- non-empty route shapes;
- equality between shape-bearing timetable route/direction keys and
  `route_shapes` keys;
- no duplicate `(trip_id, stop_sequence)` rows;
- geometry sanity and a conservative variant cap;
- required tables, columns, and indexes;
- table-count comparison with the live database, with a documented manual
  override for a legitimate major network change.

During development, a content-hash regression harness compares every relevant
table with a known-good build. This strict hash comparison is a development
gate, not a production requirement, because live sources change continually.

## Secrets and public data

`BODS_API_KEY`, `TNDS_USER`, and `TNDS_PASS` live as environment-scoped secrets
in a dedicated GitHub `timetable-build` environment. Only the build step
receives them. The workflow uses immutable action commit pins, runs with minimum
repository permissions, never runs pull-request code with secrets, redacts
credential-bearing errors, and never prints its environment. The initial shadow
run requires environment approval; that approval gate can be removed only when
the unattended path is deliberately enabled.

BODS timetable data and TNDS are published under the Open Government Licence,
so the derived timetable may be delivered through a public-repository artifact
with attribution. This decision does not extend to bustimes.org fleet data,
which is never placed in a GitHub artifact.

## Failure behaviour

Every failure is fail-closed:

- source failure: no candidate is published;
- GitHub failure: the old timetable stays live;
- missed or expired artifact: the old timetable stays live;
- download or extraction failure: the old timetable stays live;
- hash, manifest, or validation failure: the old timetable stays live;
- restart or health failure: the previous database is restored automatically.

The job record feeds `aggregate_health.py`; `status_digest.py` consumes that
single aggregate output. Alerts distinguish dispatch, build, download,
validation, promotion, restart, and rollback failures.

## Backups

The Pi keeps the live database and one `timetable.db.previous` rollback copy on
the main SSD. Nightly restic snapshots go to the separate USB drive attached to
the Pi and are copied off-device under the existing policy. GitHub artifacts
are not backup copies.

The timetable is reproducible and can later be excluded from long off-site
retention if desired. Do not change the current backup set until automated
rebuilds and a restore drill have both succeeded.

## Rollout

1. Correct and commit the plans.
2. Implement the builder optimizations and stronger validation.
3. Prove content equivalence on Windows from the same cached inputs.
4. Add the GitHub workflow with manual dispatch only; build and inspect a
   candidate without involving the Pi.
5. Install the Pi trigger/downloader in shadow mode; download and validate but
   never write the fixed upload or live paths.
6. Rehearse promotion with a disposable copy and force a health-check failure
   to prove rollback.
7. Observe one manual and two unattended shadow successes.
8. Enable live promotion and attend the first run.
9. After two successful unattended promotions, declare the laptop removed from
   production duty and update operational documentation.

Rollback at every stage is to disable the new timer/service. The existing
workstation refresh path remains available throughout.

## Alternatives retained

- **Full Pi build:** optional capped fallback trial after the primary path is
  stable. Use SSD scratch, `TMPDIR` and `SQLITE_TMPDIR`, `MemoryHigh`,
  `MemoryMax`, `MemorySwapMax=0`, and `memory.peak` measurement.
- **Google Cloud Build:** sound fallback if GitHub ceases to be suitable;
  combine Cloud Build, private Cloud Storage, and the same Pi acceptance path.
- **Automated Windows Task Scheduler:** rejected because it preserves the
  workstation dependency.

## Done means

The laptop can remain off indefinitely. The Pi notices that refresh is due,
causes a build to happen elsewhere, independently decides whether the result is
safe, promotes or rolls back atomically, records the outcome, and continues to
serve the previous valid timetable through every failure mode.
