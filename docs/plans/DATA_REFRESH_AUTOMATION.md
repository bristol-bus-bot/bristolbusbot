# Full data-refresh automation: master plan

Status: approved direction; timetable delivery implementation is in progress.
Social-media expansion is explicitly deferred until this plan is done.

The goal is not "make the Pi do everything." The goal is that the Windows
laptop can be off indefinitely without any production data silently ageing.
Heavy reproducible work runs on GitHub; the Pi detects need, controls live
state, validates candidates, promotes or rolls back, monitors outcomes, and
keeps backups.

Companion documents:

- `TIMETABLE_BUILD_MIGRATION.md`: architecture decision for timetable builds;
- `TIMETABLE_BUILD_PI_EXECUTION.md`: timetable implementation work packages;
- `SOCIAL_EXPANSION_PLAN.md`: later work, not a prerequisite here.

## System-wide principles

1. **Detection, generation, validation, and promotion are separate.** A failure
   in one never turns into permission for the next.
2. **Old valid data beats new doubtful data.** All refreshes fail closed.
3. **Generators have an explicit contract.** Inputs and candidate outputs are
   explicit; generators never write production paths.
4. **The Pi is the production authority.** External builders create candidates
   only. The Pi alone promotes them.
5. **One maintenance lock protects transitions.** Timer spacing improves load;
   locks provide correctness.
6. **Job records have one monitoring seam.** Jobs feed aggregate health; the
   digest reads aggregate health rather than every job format.
7. **Reproducible data and irreplaceable state have different retention.** Do
   not keep endless copies of rebuildable artifacts.
8. **Curated content is not silently treated as generated data.** A file needs
   a documented source, owner, and regeneration policy before automation.

## Data estate

The earlier plan omitted real consumer files. This inventory deliberately
includes generated, derived, and curated artifacts so each has an explicit
policy.

| Artifact | Source / owner | Consumers | Target policy |
|---|---|---|---|
| `timetable.db` | BODS GTFS + First TXC + TNDS | collector, site, bot | GitHub builds; Pi validates/promotes |
| `route_shapes` in timetable | same GTFS extract | site | same transaction as timetable |
| `fbribuses.json` | bustimes.org API | site, bot | Pi refresh, private, fail closed |
| `bus-descriptions.json` | Gemini plus human policy | site | incremental, gated automation |
| `depot-descriptions.json` | Gemini plus human policy | site | incremental, gated automation |
| `waiting-descriptions.json` | Gemini plus human policy | site | incremental, gated automation |
| `blurb_scope.json` | collector `live.db` + `audit.db` | blurb generators | Pi-derived, identity-safe |
| `stop_localities.json` | timetable stops + ONS boundaries | site, bot | triggered Pi derivation |
| `stop_enrichment.json` | legacy/open-data enrichment | site, bot | versioned/manual until generator is restored and tested |
| `local_flavour.json` | curated editorial geography | bot | versioned/manual; never auto-overwrite |
| `route_details.json` | derived route context; generator not in current public source | bot | keep versioned until a reproducible generator exists |
| `weca_boundary.geojson` | ONS geography, four features | site | manual approval, versioned |
| `weca_boundary_dissolved.geojson` | same geography, dissolved polygon | timetable builder, site | same approved boundary transaction |
| audit integration snapshot | audit databases and rollup | site, bot, monitoring | existing recorded/atomic publication path |

`local_flavour.json`, `route_details.json`, and `stop_enrichment.json` are not
automated merely because they are JSON. Their current sources and ownership are
not equivalent. The master audit reports their presence and age, but no job may
regenerate or remove them until their policy above changes deliberately.

## Target architecture

```text
                         reproducible heavy work
authoritative sources  --------------------------> GitHub Actions
       |                                             |
       | lightweight/private work                   | timetable candidate
       v                                             v
Raspberry Pi detectors -> Pi regenerators -> Pi staging/validation
                                                |
                                                v
                                  atomic promotion + health gate
                                                |
                                  live data or automatic rollback
                                                |
                                    job record -> aggregate health
                                                |
                                           status digest
```

The data plane lives under `/var/lib/bristolbusbot`, independently of immutable
code releases. Code deployment does not refresh data, and data promotion does
not deploy code.

## Phase A - Timetable automation

Complete WP1-WP8 in `TIMETABLE_BUILD_PI_EXECUTION.md`:

- optimized and deterministic builder;
- stronger validator and provenance manifest;
- GitHub candidate build;
- Pi trigger, download, independent validation, promotion, rollback;
- monitoring, backup, shadow runs, and cutover.

This is the priority because every other timetable-derived artifact depends on
it. A full build on the 1 GB Pi is optional fallback work, not a gate.

Acceptance: two unattended promotions pass their health gates and the laptop is
no longer part of normal timetable production.

## Phase B - Identity and complete consumer-path audit

Do this before fleet and blurb automation.

### Vehicle identity

The current data has 71 duplicated active fleet-code groups. A fleet code alone
is not a safe cross-operator identity, and `build_blurb_scope()` currently
collapses references with `ref.split("-")[-1]`.

Adopt the following model:

- registration is the canonical vehicle identity where present;
- a source-stable identifier is the fallback when registration is absent;
- `(NOC, fleet_code)` is the operator-scoped lookup/display identity;
- a bare fleet code is never a global key.

First audit whether collisions cause wrong descriptions in production today.
Then introduce dual-key reads, migrate keyed files deliberately, update site and
bot together, and remove the lossy reference collapse only after fixtures cover
multi-operator collisions.

### Consumer paths

The site already has environment overrides for its enrichment files. The bot
still opens several working-directory or compiled-relative paths directly.
Add tested environment overrides for every bot-consumed artifact, including
fleet, localities, stop enrichment, local flavour, and route details. Do not use
symlinks into immutable release directories.

Acceptance: a grep-backed inventory proves where every consumer reads every
artifact, and identity fixtures cannot cross-wire equal fleet codes from
different operators.

## Phase C - Data/code decoupling

1. Create `/var/lib/bristolbusbot/enrichment/`, owned by the data-maintenance
   service and included in the backup configuration.
2. Seed it from the verified live release.
3. Point one consumer and one artifact at a time to the durable paths via
   environment variables.
4. Restart and health-check after each change.
5. Add a Pi-side data promotion helper: fixed staging name, artifact-specific
   validation, `.previous`, atomic replace, consumer restart, health gate, and
   rollback under the maintenance lock.
6. Only after all consumers use durable paths, forbid those mutable data files
   from new code-release packages.

Per-artifact gates include schema, required keys, size ceilings, count-change
limits, and cross-checks against current production data. A legitimate major
change requires a named manual override that is recorded; deleting the current
file is not the override procedure.

Acceptance: a code deploy leaves enrichment unchanged, a data refresh changes
no code release, and a forced promotion failure restores the previous artifact.

## Phase D - Unified data-health audit

A nightly read-only Pi job answers:

- Is the timetable service horizon shrinking or its build overdue?
- Did the last GitHub build, download, validation, or promotion fail?
- Is the GitHub dispatch credential nearing expiry?
- Is fleet data old or drifting from vehicles actually observed?
- Are active observed vehicles missing operator-scoped descriptions?
- Did a new timetable introduce stops without locality data?
- Are curated/reference files missing or unexpectedly changed?
- What boundary edition produced the current timetable and audit exports?

The job writes one versioned JSON report through `run_recorded_job.py`.
`aggregate_health.py` consumes job records and `status_digest.py` consumes only
aggregate health. Thresholds live in one configuration block.

Acceptance: report-only mode runs for two weeks with counts that can be checked
by eye, and injected failures appear as useful digest sentences.

## Phase E - Deterministic Pi regenerators

### Fleet

Refactor `update_fleet_data.py` before scheduling it:

- explicit input/output paths and zero production writes;
- honest User-Agent, bounded retries, timeouts, and existing polite pacing;
- no catch-print-break partial success;
- every configured operator receives a source-stage result;
- compare per-operator active counts with the previous file;
- reject a successful HTTP response containing an unexplained empty or
  collapsed operator result;
- stage, validate, and promote only the complete combined file;
- never publish the fleet file through GitHub or the public repository.

Run weekly and when the audit detects drift. Source failure leaves the old fleet
file live.

### Localities

After a successful timetable promotion, the audit checks for stops without
localities. If needed, run `geocode_stops.py` against the exact live timetable
and approved boundary edition, write a candidate, require coverage and count
gates, and promote independently.

### Boundaries

Boundary changes remain human-approved because they redefine what is inside the
audited area. A boundary refresh produces both combined and dissolved files as
one versioned transaction. Stamp the geography edition and hash into the
timetable manifest and published audit exports so historical numbers remain
traceable.

### Other reference files

Do not automate `route_details.json` or `stop_enrichment.json` until their
generator source is present, documented, deterministic, and covered by the same
candidate contract. `local_flavour.json` remains curated.

Acceptance: each deterministic job completes a shadow cycle, comparisons are
reviewed, and a failed source or validator cannot change live data.

## Phase F - AI description generation

This happens only after vehicle identity and deterministic refreshes are stable.

- Generate only missing descriptions for observed, active, operator-scoped
  vehicles.
- Put hard per-run and monthly cost limits in configuration.
- Treat all community-source fields as untrusted prompt data: normalize,
  length-limit, and frame them as data rather than instructions.
- Accept only requested keys and enforce JSON schema, length, URL/handle/HTML,
  profanity, and unchanged-existing-entry checks.
- Discard the entire candidate batch when any output fails.
- Keep new descriptions in a pending file for at least the first 30 days and
  require human approval.
- Consider auto-promotion only after a clean evidence window; absence of a
  description must always remain cosmetic.

Acceptance: adversarial fixtures pass, costs are bounded, a month of pending
batches has been reviewed, and existing descriptions cannot be removed by a
failed run.

## Scheduling and locks

Avoid one mega-job. Each stage is idempotent, separately recorded, and triggered
by state:

```text
timetable due -> GitHub build -> Pi promotion -> locality gap check
fleet due/drift -> fleet candidate -> identity-safe blurb scope
missing blurbs -> pending AI batch
boundary age -> report only -> human-approved refresh
```

Backup has priority over heavy maintenance. Timetable delivery, code deploy,
backup, and conflicting data promotions share the Pi maintenance lock. A job
waits with a deadline and records a named refusal instead of running late into
another job.

## Backups and retention

- Main SSD: live artifact plus one `.previous` rollback copy.
- External USB drive: encrypted restic snapshots, integrity checks, and restore
  drills.
- Off-device storage: retain irreplaceable audit history, bot state, config, and
  other disaster-recovery material under the existing policy.
- GitHub: seven-day delivery artifact only.
- Manifests and job records are small and retained longer than rebuildable
  timetable binaries.

Do not remove the timetable from the existing restic set until cloud building,
Pi promotion, and a restore drill have all proved reliable.

## Rollout order

1. Phase A: timetable delivery.
2. Phase B: identity and consumer paths.
3. Phase C: decouple data from code releases.
4. Phase D: health audit in report-only mode.
5. Phase E: deterministic fleet and locality regeneration, shadow first.
6. Phase F: descriptions with a human approval window.
7. Only then return to Threads, Instagram, and the wider social plan.

When social work resumes, first correct its stale command-centre reference and
keep OAuth callback routing through the core bot rather than a separate public
administration surface.

## Risk register

| Risk | Detection | Mitigation |
|---|---|---|
| external builder unavailable | overdue build health | old data stays live; workstation/GCP fallback |
| source returns partial data | source-stage and count gates | discard whole candidate |
| source publishes overlapping route revisions | route-edition window validation | preserve revisions but prevent replacement editions being active together |
| fleet-code collision | identity audit fixtures | canonical registration plus `(NOC, fleet_code)` |
| bot still reads release data | consumer-path audit | tested environment overrides before cutover |
| source changes legitimately exceed limits | named refusal | recorded manual override with review |
| boundary change rewrites history | edition/hash mismatch | provenance in manifests and exports |
| AI output is unsafe or off-brand | deterministic gates, pending review | whole-batch discard and human window |
| GitHub artifact mistaken for backup | restore drill | external restic remains backup |
| job timers overlap | lock contention record | shared lock and deadlines |
| enrichment directory omitted from backup | backup manifest test | explicit include plus restore fixture |

## Done means

The laptop can be off for a month or longer. The Pi notices ageing or missing
data, causes safe regeneration in the appropriate compute plane, validates and
promotes candidates atomically, keeps the old version through failures, records
plain-language outcomes, and preserves enough provenance to explain every live
artifact. Human duties are limited to exceptional overrides, boundary approval,
the initial AI-description review window, credential renewal alerts, and reading
the health digest.
