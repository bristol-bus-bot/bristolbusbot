# Timetable delivery execution plan: GitHub build, Pi promotion

Status: approved plan; implementation in progress.

The filename is retained because earlier discussions and documentation link to
it. This is no longer a plan to make the production Pi perform the normal full
build. It is the engineer-facing implementation plan for the architecture in
`TIMETABLE_BUILD_MIGRATION.md`.

## Ground rules

1. Production remains unchanged through WP1-WP6. Shadow code cannot address
   `/var/lib/bristolbusbot/pipeline/.timetable.db.upload` or the live database.
2. All generated output is disposable until the Pi has independently accepted
   it.
3. No stage may soften a source or validation failure to obtain a successful
   result.
4. Every run leaves structured evidence: run identity, stage timings, source
   provenance, validation result, and a named failure.
5. Only the Pi may promote production data.

## Known implementation hazards

### H1 - implicit paths and production writes

Several scripts currently rely on repository-relative paths, environment
defaults, or a conventional temporary directory. Refactor their callable entry
points to accept explicit source, boundary, fallback, scratch, and output paths.
The orchestration layer rejects output paths outside its candidate directory.

### H2 - memory-heavy downloads and parsing

The First TXC fetch currently obtains complete HTTP response bodies in memory,
and TXC merge code reads and decodes complete ZIP members before parsing. Live
unattended builds must stream downloads to disk, verify the archive, and pass a
ZIP member's file-like stream to the parser where supported.

### H3 - incomplete sources can look successful

No catch-print-break path may publish the data gathered before a source failed.
Every required source gets an explicit success record and plausibility checks.
TNDS, First TXC, and BODS failures abort the whole timetable build.

### H4 - shape output is order-sensitive

The shape clustering algorithm is greedy. Changing floating-point arithmetic,
query ordering, or the representative selection can change the output while
still looking plausible. Keep the current algorithm and arithmetic. Move raw
points to a file-backed SQLite temporary table, make input ordering explicit,
and prove exact `route_shapes` equality with the regression harness.

### H5 - validator coverage is too shallow

The current validator checks integrity, journal mode, required First routes,
latest service date, and a non-zero shape count. It would accept several
structurally wrong databases. Add key-set, duplicate, geometry, schema, count,
and source-completeness gates before relying on unattended promotion.

### H6 - artifact selection is a trust decision

Do not download "the newest artifact named timetable" across the repository.
Select a successful run of the exact workflow on the default branch, validate
the allowed event and commit, obtain the artifact from that run, and remember
the consumed run ID. Reject ZIP traversal, symlinks, unexpected filenames,
oversized files, and unsupported manifests.

### H7 - GitHub schedules are not a sufficient control plane

The Pi detects refresh need and dispatches `workflow_dispatch`. A narrowly
scoped Actions-write token is stored root-only under `/etc/bristolbusbot` and
its expiry is monitored. The dispatch has no user-controlled build inputs. A
GitHub schedule is secondary only.

### H8 - promotion crosses privilege boundaries

The fetcher/downloader is unprivileged and has no restart rights. A separate
privileged oneshot helper accepts only a fixed staging path and performs final
validation, atomic promotion, restart, health check, and rollback under a
single Pi-owned maintenance lock.

### H9 - timer spacing is not mutual exclusion

The existing backup, rollup, publish, and sampling timers already occupy the
night. Choose a quiet timetable-delivery window, but require conflicting jobs
to take the maintenance/heavy-I/O lock. Backup has priority; a delivery waits
with a deadline and records a named failure if it cannot start.

### H10 - dates must mean Bristol dates

Set `TZ=Europe/London` for freshness decisions and record timestamps in UTC in
machine-readable manifests and job records.

## Work packages

### WP1 - Build contract and regression fixtures

- Extract a callable build interface with explicit paths and a no-download
  mode.
- Define required source-stage results and manifest schema version 1.
- Add a developer command that builds from one frozen source set and emits
  deterministic table hashes and row counts.
- Preserve a known-good result for comparison without committing source data or
  the generated database.

Acceptance: a test proves the build cannot resolve or write the production
database implicitly, and missing source stages produce a non-zero named error.

### WP2 - Resource and database optimizations

Implement, separately and with a regression check after each change:

1. stream filtered GTFS CSV rows and batch inserts;
2. avoid retaining the complete trip-to-route map when a second cheap scan is
   sufficient;
3. insert only retained stops rather than inserting all and deleting most;
4. use a bounded SQLite cache and file-backed temporary storage;
5. use unsafe fast pragmas only for the disposable candidate;
6. use a file-backed temporary `shapes` table and keep the existing clustering
   arithmetic and ordering;
7. omit the permanent raw `shapes` table;
8. remove `idx_stop_times_trip` and `idx_trips_route`, whose composite indexes
   already provide the same left prefix;
9. stream the BODS/First downloads and TXC ZIP entries;
10. finalize with `ANALYZE`, `PRAGMA optimize`, safe journal settings, and a
    compact static database.

Acceptance: every relevant table and `route_shapes` has the same content hash
as the known-good frozen-input build, all tests pass, and peak RSS/database size
remain close to or below the measured 193 MiB/197 MiB results.

### WP3 - Validation and provenance

Extend `timetable_control.py` or a shared validation module with:

- schema and required-index checks;
- duplicate `(trip_id, stop_sequence)` rejection;
- service-horizon minimum, not merely "not already stale";
- route and trip count floors;
- required First routes;
- route-shape key-set equality;
- valid LineString geometry, coordinate bounds, minimum point count, and a
  conservative variants-per-route cap;
- optional comparison with a previous database, including per-table count
  collapse limits;
- machine-readable result output.

Create `manifest.json` only after validation succeeds. Re-open the finished
database read-only and calculate its final hash after all finalization steps.

Acceptance: corrupt, incomplete, duplicate, shapeless, stale, implausibly small,
and manifest-mismatched fixtures are each refused for the expected reason.

### WP4 - GitHub build workflow

Add a dedicated workflow separate from ordinary PR CI:

- triggers: `workflow_dispatch` first; optional off-hour `schedule` later;
- default branch only, no caller-supplied source URLs or commands;
- one concurrency group with overlap refused rather than silently replacing a
  running build;
- pinned Python and locked build dependencies;
- minimum GitHub permissions;
- BODS/TNDS credentials from the dedicated `timetable-build` GitHub
  environment, exposed only to the build step;
- immutable full-commit pins for every reused GitHub action;
- bounded network retries, timeouts, size ceilings, archive tests, and an honest
  User-Agent;
- build, validate, manifest, and upload in that order;
- artifact contains only the three approved files and expires after seven days;
- failure summary names the stage without exposing secrets.

Acceptance: a manual workflow run produces a candidate that passes a clean
local download, manifest verification, and validation. No Pi service is changed.

### WP5 - Pi trigger and downloader in shadow mode

Add an unprivileged Pi service and timer that:

- reads current timetable health and dispatches only when refresh is due;
- detects an existing/recent run and does not produce duplicate builds;
- can re-enable the workflow if GitHub disabled it for inactivity;
- records dispatch/run IDs and polls with bounded backoff;
- selects only a successful accepted workflow run;
- downloads into SSD staging with byte and time limits;
- extracts safely and verifies the manifest and database hash;
- runs independent local validation and current-vs-candidate comparisons;
- records success/failure through `run_recorded_job.py`;
- in shadow mode cannot address the fixed upload or live paths.

The GitHub token is repository-scoped with Actions write only, root-readable,
never logged, and has an expiry warning in aggregate health.

Acceptance: repeated timers are idempotent; a second process cannot duplicate a
download; malformed API data, expired artifacts, unsafe ZIPs, bad hashes, and
validation failures all leave production untouched.

### WP6 - Pi promotion transaction

Add a privileged oneshot promotion helper that:

- accepts no arbitrary source or destination path;
- takes the shared maintenance lock;
- confirms the staged file is regular, owned as expected, and not a symlink;
- repeats final validation;
- retains one hard-linked or copied `timetable.db.previous`;
- atomically replaces the live database;
- restarts collector, site, and bot through allowlisted helpers;
- checks systemd state and public/local health endpoints;
- restores the previous database and restarts again on failure;
- writes an accepted or rolled-back record before releasing the lock.

Keep the build/download unit sandboxed with no promotion or restart rights. The
promoter does not make network calls or parse source archives.

Acceptance: forced failures before replace, after replace, during restart, and
during health check all produce the expected live file and job record.

### WP7 - Monitoring, backups, tests, and runbook

- Teach `aggregate_health.py` to consume the timetable job record.
- Keep `status_digest.py` dependent on aggregate health only.
- Alert on source/build failure, refresh overdue, token nearing expiry, workflow
  disabled, artifact unavailable, validation refusal, rollback, and shrinking
  service horizon.
- Put download, validation, promotion, backup, and manual refresh under the
  documented lock order.
- Keep live plus `.previous` on the main SSD and include the live database in
  restic snapshots to the external drive.
- Add unit-file tests for users, permissions, sandboxing, credentials, timeouts,
  locks, and shadow/live separation.
- Document manual GitHub dispatch, workstation fallback, token rotation,
  override of a legitimate count collapse, and complete disable/rollback.

Acceptance: the digest reports a deliberately injected failure in plain
language, and a restore drill recovers a timetable independently of GitHub.

### WP8 - Evidence-gated rollout

1. Run one GitHub build manually and inspect the artifact and logs.
2. Install the Pi trigger/downloader with promotion structurally disabled.
3. Complete one attended and two unattended shadow deliveries.
4. Compare service horizon, row counts, route keys, shapes, size, and query
   plans with the current production database.
5. Rehearse promotion against a disposable root and force rollback.
6. Enable production promotion and attend the first run.
7. Require two consecutive unattended promotions before removing the laptop
   from normal production duty.
8. Update `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `pipeline/README.md`,
   `deploy/README.md`, and the roadmap with the proven state.

At every point, rollback is disabling the new timer/service. The current manual
workstation path remains available.

## Risk register

| # | Risk | Detection | Mitigation |
|---|---|---|---|
| R1 | source download is truncated or huge | byte limits, ZIP test | streaming, retries, fail closed |
| R2 | one required source silently disappears | source-stage manifest | hard failure; no artifact |
| R3 | optimized build changes data | frozen-input hashes | one change at a time; exact regression |
| R4 | greedy shape order changes variants | route-shape hash/key checks | explicit order; unchanged arithmetic |
| R5 | GitHub workflow runs attacker-controlled code with secrets | event/ref audit | default branch only; never PR code |
| R6 | Pi downloads the wrong artifact | run metadata checks | exact workflow/run selection |
| R7 | malicious or corrupt ZIP reaches the Pi | safe extractor tests | allowlist, limits, hashes, no symlinks |
| R8 | GitHub schedule is delayed or disabled | age/disabled-state health | Pi dispatch plus manual fallback |
| R9 | Pi token expires or leaks | expiry alert; secret scan | least privilege, root-only, rotation |
| R10 | candidate is valid but implausibly incomplete | previous-db comparison | count floors and manual override |
| R11 | promotion races backup or deploy | lock contention record | one Pi-owned maintenance lock |
| R12 | restart succeeds but application is broken | local/public health gates | automatic rollback |
| R13 | artifact is mistaken for backup | restore drill | Pi restic remains authoritative backup |
| R14 | public artifact includes non-redistributable data | artifact-content test | timetable and attribution only |
| R15 | 1 GB Pi cannot validate comfortably | shadow `memory.peak` | validation limit; old DB stays live |

## Optional full-Pi fallback trial

Only after the primary path is stable, a cached-input full build may be tested
on the Pi with promotion disabled. It uses the optimized builder, SSD-backed
`TMPDIR` and `SQLITE_TMPDIR`, `MemoryHigh`, `MemoryMax`, `MemorySwapMax=0`, idle
I/O priority, a runtime limit, and `memory.peak` measurement. Failure or a
service-health impact merely confirms that GitHub remains the build plane.

## Done means

All work packages have passed their acceptance gates; two unattended live
promotions have succeeded; failure and rollback have been demonstrated; the
laptop is documented only as a fallback; and the Pi remains healthy throughout.
