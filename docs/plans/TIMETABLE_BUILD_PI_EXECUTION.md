# Timetable delivery execution plan: GitHub build, Pi promotion

Status: enabled, live and fully proved. The first scheduler-triggered due
delivery on 29 July failed safely before promotion and exposed validator and
monitoring defects. Their corrections passed review, an attended diagnostic
shadow, exact-hash promotion and harmless automatic follow-up on and after
30 July 2026 as run `30568434088`.

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

No catch-print-break path may publish the data gathered before a required
source failed. BODS GTFS and First TXC are the primary sources. Their combined
output is checked against the explicit completeness contract. TNDS is used only
when that check finds required routes missing; if needed, its download or merge
failure aborts the whole build. The manifest records either `fallback_used`
with the missing-route reason or `not_needed` after a successful primary-source
check.

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
- bounded network retries, timeouts, size ceilings, resumable TNDS transfer,
  progress diagnostics, archive tests, and an honest User-Agent;
- build, validate, manifest, and upload in that order;
- artifact contains only the three approved files and expires after seven days;
- failure summary names the stage without exposing secrets.

Acceptance: a manual workflow run produces a candidate that passes a clean
local download, manifest verification, and validation. No Pi service is changed.

Implementation evidence (2026-07-22): GitHub run `29903848166` completed from
the merged default-branch commit in about three and a half minutes. Its exact
three-file artifact passed a clean Windows download, manifest/hash verification,
and the independent production validator. TNDS was recorded as `not_needed`.

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

Implementation status (updated 2026-07-29): code and hostile-input tests are complete.
The root-installed Python entry point has no promotion action or destination
argument; the systemd units grant write access only to the shadow and monitoring
directories. The timer uses a dedicated automatic unit whose successful result
may chain to `promote@auto`; an attended `@RUN_ID` unit has no promotion edge.
The daily timer is installed disabled until its
repository-scoped credential exists. Its 05:00 window follows the 04:30 Sunday
backup check, and both share the heavy-I/O lock so the backup has precedence.
Automatic checks use the last successful shadow delivery as the freshness
clock: success starts a six-day cooldown, yielding about one build per week,
while a failed due run retries the next day. The service-coverage horizon is a
safety signal, not a reason to leave frequently changing source data stale.
Pi installation and two attended shadow deliveries completed on 2026-07-22.
The daily timer is enabled, its recent-shadow no-op path was exercised, and the
GitHub environment reviewer gate was removed while the default-branch-only
policy remained in place. The corrected templates assign lock timeout exit 73,
retain 75 for an application skip, and fast-skip an already handled candidate
before database revalidation. They are not installed yet.

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
promoter never downloads timetable data or parses source archives. Its only
outbound request is the fixed public production health check after restart.

Implementation status (2026-07-22): the fixed-path promoter, root-only enable
marker, separate systemd sandbox, monitoring seam and failure-injection tests
are implemented. Laptop tests force failures before replacement, after
replacement, during each consumer restart/health gate and at public health;
post-replacement failures restore the old database. The same rejected artifact
is not retried automatically.

First live-trial evidence (2026-07-22): the candidate passed every database
gate and was atomically installed. The trial exposed two Pi-specific health
assumptions: the collector recovery probe exceeded its original 15-second
subprocess timeout, and Cloudflare returned HTTP 403 to Python's default
`urllib` User-Agent. The transaction restored the old database. The probe
timeout was raised to 45 seconds with six bounded attempts, the health client
now sends an explicit project identity, and the transaction ceiling was raised
to 20 minutes so rollback retains its own complete recovery window. Automatic
promotion remained disabled.

Consumer-regression evidence (2026-07-22): after the corrected transaction was
accepted, the larger candidate exposed a site query that recomputed every
stop-to-route relationship from 1.96 million `stop_times` rows. Gunicorn killed
the request at 30 seconds and the public endpoint returned HTTP 502, while the
shallow `/healthz` gate still passed. The known-good timetable was restored and
automatic promotion was paused. New candidates now materialise `stop_routes`
after all source merges, the site reads that compact table, the browser performs
only one bounded retry, and promotion calls the real stop-search endpoint with
a minimum-result and 20-second gate. A full cached-input build produced 14,670
lookup pairs; the complete website search payload assembled in about 0.052
seconds on the Windows verification run. Detailed Slack notifications now
distinguish accepted, rejected, rolled-back and rollback-failed outcomes.

Source-edition evidence (2026-07-22): the fresh BODS aggregate grew from
1,183,343 to 1,965,256 stop-time rows almost entirely because First Bristol
published overlapping current and future revisions under the same route IDs.
The records were structurally unique but many revisions represented the same
service period, so duplicate-key checks could not detect the problem. The
candidate builder now records each route edition, gives replacement-like
revisions non-overlapping effective windows, and retains small or differently
scheduled cohorts as possible genuine additions. The independent validator
recomputes those windows and rejects unresolved replacement overlaps. Collector,
site and bot matching apply calendar ranges plus dated additions/removals; Slack
success includes the number of editions separated. A laptop copy of production
rewindowed 30,817 trips across 146 superseded editions: route 75 fell from two
active Sunday editions on 26 July to one, and from three on 30 August to one,
without deleting any source journey or stop-time records.

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

Implementation status (updated 2026-07-29): timetable delivery and promotion
job records feed aggregate health, the live and `.previous` files remain in the
encrypted backup set, and the existing local/off-site restore procedure
provides the independent recovery path. The first due failure exposed two
monitoring defects: promotion was treated as independently overdue after its
prerequisite shadow failed, and the resulting alert reused an older successful
`no_change` detail, producing `unknown_failure`. The source correction now
models one run/hash-correlated automation transaction, exposes it in the digest,
keeps accepted identity separate from the latest attempt, names lock failures,
and advances alert deduplication only after Slack confirms delivery. Pi rollout
evidence remains outstanding.

### WP8 - Evidence-gated rollout

1. Run one GitHub build manually and inspect the artifact and logs.
2. Install the Pi trigger/downloader with promotion structurally disabled.
3. Complete attended shadow deliveries and exercise the automatic timer/no-op
   path. The maintainer explicitly chose not to wait a week for additional
   shadow-only evidence after two clean Pi validations.
4. Compare service horizon, row counts, route keys, shapes, size, and query
   plans with the current production database.
5. Rehearse promotion against a disposable root and force rollback.
6. Enable production promotion and attend the first run.
7. Observe subsequent unattended promotions; the laptop remains an emergency
   fallback while live automation accumulates operating history. Run
   `29944744744` was accepted by the production `auto` path on 22 July 2026,
   but was manually initiated during commissioning. The first timer-triggered
   due delivery, run `30421182234` on 29 July, built correctly and was rejected
   before promotion by an over-broad raw `stop_times` count gate. Production
   remained healthy. The validator and monitoring corrections in the incident
   review below are now the remaining evidence gate.
8. Update `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `pipeline/README.md`,
   `deploy/README.md`, and the roadmap with the proven state.

At every point, rollback is disabling the new timer/service. The current manual
workstation path remains available.

## 29 July 2026 incident review and correction package

The first genuinely due timer run proved the fail-closed boundary: the GitHub
build succeeded, the Pi rejected the candidate, and no production file was
replaced. It also showed that safe refusal is not the same as a good decision.

### Evidence

| Measure | Live accepted run `29944744744` | Candidate run `30421182234` |
|---|---:|---:|
| Routes | 254 | 245 |
| Trips | 54,506 | 42,050 |
| Stops | 6,437 | 6,416 |
| Stop times | 1,965,256 | 1,470,423 |
| Route shapes | 413 | 402 |
| First routes | 108 | 108 |
| Stop-route pairs | 15,153 | 15,014 |
| Recorded route editions | 352 | 286 |
| Superseded editions | 146 | 74 |
| Latest service | 30 May 2027 | 4 June 2027 |

The flat `stop_times >= 75% of live` rule required 1,473,942 rows, so the
candidate failed by 3,519 rows at 74.82%. A read-only semantic comparison found
the candidate at 100.3% of live for both trips and stop times over the next 7,
14 and 28 days. Across 306 common future service dates it was lower on only
four dates, with a minimum daily ratio of 95.1% on New Year's Day. First
Bristol's 28-day stop times increased from 5,535,344 to 5,545,088. The raw
source and database were smaller mainly because fewer obsolete/superseded
editions were retained, not because current service had collapsed.

At 11:06 the monitor then sent a separate “promotion rejected” alert for the
previous day. No such rejection occurred: the most recent promoter record was
a successful `no_change`. `aggregate_health.py` independently aged promotion
at 30 hours even though the failed shadow correctly prevented promotion, then
formatted the older detail as `unknown_failure`.

### Required implementation

1. Build a read-only service profile for the live and candidate databases in a
   single bounded scan. Aggregate by service date, operator and route; derive
   28-day plausibility and at least 180 days of forward coverage in Python.
2. Make service-window semantics the primary completeness gate. Initial
   conservative policy: each near-term day retains at least 85% of live trips
   and stop times; the 28-day total at least 90%; routes at least 80%; each
   substantial operator at least 70%; and no forward-coverage cliff below 75%.
   Fixtures may justify adjusting those figures before merge, but all values
   live in one versioned policy and are written to the attempt record.
3. Keep stable structural/inventory checks for routes, stops, shapes, First
   routes and stop-route pairs. Retain raw trip/stop-time counts as diagnostics
   or a low emergency floor, not the main acceptance decision. A legitimate
   operator transfer may be a warning only when equivalent dated service
   demonstrably reappears under another NOC; matching a route number alone is
   insufficient.
4. Extend structured records with allowlisted numeric comparison details,
   run/hash identity and timings. Preserve the last accepted database metadata
   separately from the last attempt. Never forward raw exception text,
   downloaded strings, credentials or host paths to Slack.
5. Treat shadow and promotion as one causal incident. Promotion is expected
   only after a new successful shadow. Correlate wrapper and detail records by
   run/hash/time; do not reuse an older success to explain a newer failure.
   Deduplicate each attempt fingerprint, retry failed Slack sends, and recover
   only when the whole timetable incident family is clear.
6. Give lock timeout its own failure exit and code; exit 75 remains a benign
   application skip. Avoid chaining the promoter after a recent-shadow/no-new-
   candidate skip, which currently spends about a minute revalidating the old
   candidate.
7. Make attended delivery genuinely attended and pinned. An exact-run shadow
   must not silently chain to `promote@auto`, and the reviewed run/hash must be
   the one the attended promoter accepts.
8. Strengthen the live health transaction: retain database/hash checks,
   collector verification, stop-search and public health, and also require the
   bot's nested health payload to report its timetable connection.

The comparator must open SQLite databases read-only/immutable with
`query_only`, a bounded page cache and file-backed temporary storage. It uses
fixed SQL, has an internal deadline, and must not create a second BODS consumer.
Do not guess a `MemoryMax`: measure peak memory, available RAM and swap on a
promotion-disabled Pi shadow first.

### Required regression and rollout evidence

- The exact 22/29 July pair passes the new semantic comparison.
- Missing near-term day, missing material operator, stale/empty future,
  malformed calendar, oversized/slow database and future coverage cliff fail.
- An operator transfer with equivalent dated service warns but passes; an
  unrelated same-number route cannot mask a loss.
- Bank holidays, `calendar_dates`, Bristol local dates and DST are fixtures.
- Lock timeout, different consecutive failures, Slack retry/deduplication,
  state-schema migration and every rollback injection are tested.
- After merge, build a fresh artifact. Stop the timer and disable automatic
  promotion; install the control-plane change; run an exact shadow; inspect the
  comparison and Pi resource peak; then perform one attended promotion. Verify
  the live hash and `.previous`, collector, site, stop search, bot timetable
  connection, public health, Slack, aggregate health and digest before restoring
  the marker and timer. No week-long wait is required.

## Risk register

| # | Risk | Detection | Mitigation |
|---|---|---|---|
| R1 | source download is truncated or huge | byte limits, ZIP test | streaming, retries, fail closed |
| R2 | one required source silently disappears | source-stage manifest | conditional TNDS fallback or hard failure; no partial artifact |
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

The architecture and original work packages went live on 22 July 2026, with an
attended promotion, rollback proof and automatic no-change exercise. The first
scheduler-triggered due delivery on 29 July failed safely but exposed a false
negative completeness gate and a false downstream alert. The correction package
passed as run `30568434088`: attended diagnostic shadow at 18:05–18:08 UTC on
30 July, separate exact-hash promotion at 18:11–18:13 UTC, live SHA-256
`611c55cd1a381a49e781c6ab58ce363076e3865de8aacc58477d28112e54d068`,
previous database retained, aggregate health and digest healthy, and harmless
automatic no-update exercises through 5 August. Phase A is complete and the
laptop remains fallback only.
