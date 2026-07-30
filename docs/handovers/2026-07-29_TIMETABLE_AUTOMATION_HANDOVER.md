# Handover: timetable automation incident and next work

Prepared 29 July 2026 for a fresh working chat. This is an implementation
handover, not evidence that the correction below has already been deployed.

## Read this first

The live website, collector and bot are healthy and still use the timetable
accepted on 22 July 2026. The first genuinely scheduler-triggered due candidate
was built successfully on 29 July but the Pi rejected it before production.
That was safe. The rejection decision was overly conservative, and the later
“promotion rejected” alert was false. The next job is to correct those two
defects without weakening the security and rollback design.

There is one human maintainer. Do not assume that another maintainer exists or
can review or approve work. Code review and production decisions remain with
that maintainer. Outside suggestions may be useful planning input but are not
approval or operational evidence.

At the time of this handover, `main` was commit `9335b9f`. Documentation edits
describing this incident were uncommitted. Start with `git status` and preserve
them. Do not change the Pi, merge or deploy merely because this handover exists.

## Plain-English diagnosis

The safety check compared the total number of timetable rows in the new and old
databases. The new source kept fewer obsolete copies of old/replaced timetables,
so it looked about 25% smaller even though the buses due to run now and over the
coming weeks were all present. It missed the existing threshold by only 3,519
stop-time rows. The Pi did exactly the safe thing and kept the old timetable.

The right fix is not to lower the threshold. Compare the actual service that can
run on each upcoming date, broken down by operator and route. Keep the raw size
check as a diagnostic or very low emergency floor. A new file should fail if it
loses a day, an operator or a large part of future service, regardless of how
many historical rows it contains.

The second Slack alert was monitoring confusion. Promotion is supposed to run
only after delivery succeeds. Delivery failed, so no promotion was attempted.
About 30 hours after the previous successful no-change promotion, monitoring
declared promotion “overdue”, paired that issue with the old success detail and
called it `unknown_failure`. Delivery and promotion need to be monitored as one
causal transaction.

## Confirmed evidence

### 29 July build and rejection

- Alerted GitHub run: `30421182234`.
- Workflow started `2026-07-29T04:02:36Z` and completed successfully at
  `04:05:48Z` from commit `9335b9f`.
- Pi shadow finished at about 05:07 BST after 327.927 seconds with
  `candidate_count_collapse`.
- Candidate never reached production.
- Existing production run: `29944744744`, commit starting `5bf9e697`, database
  SHA-256 starting `c42a857`.
- Candidate SHA-256 starts `58dce7b`.

| Validation measure | Production | Candidate | Candidate / production |
|---|---:|---:|---:|
| Routes | 254 | 245 | 96.5% |
| Trips | 54,506 | 42,050 | 77.1% |
| Stops | 6,437 | 6,416 | 99.7% |
| Stop times | 1,965,256 | 1,470,423 | 74.82% |
| Route shapes | 413 | 402 | 97.3% |
| First routes | 108 | 108 | 100% |
| Stop-route pairs | 15,153 | 15,014 | 99.1% |
| Route editions | 352 | 286 | 81.3% |
| Superseded editions | 146 | 74 | 50.7% |
| Latest service | 30 May 2027 | 4 June 2027 | later |

The old rule is `candidate >= int(production * 0.75)` for trips and stop
times. The stop-time minimum was 1,473,942; the candidate was 3,519 rows short.
No other raw count gate failed.

The source evidence supports a real edition cleanup: the BODS raw stop-times
file fell from 615,669,383 to 519,002,518 bytes, many First TXC archives were
smaller, all 34 required datasets remained, and the First-route result stayed
at 108.

### Service that can actually run

An exact read-only comparison of both artifacts found:

- next 7 days: candidate trips and stop times were 100.3% of production;
- next 14 days: 100.3%;
- next 28 days: 100.3%;
- 306 common future service dates; candidate was lower on only four;
- lowest daily ratio was 95.1% on New Year's Day;
- First Bristol 28-day stop times increased from 5,535,344 to 5,545,088;
- KEMT route Y2C moved from Kempsford to Eurocoaches;
- ABUS 101, First 1s/125s and Stagecoach West 40 appeared;
- The Big Lemon retained about 97%; other material operators were unchanged or
  higher.

Inference: the candidate is plausibly complete. This evidence is strong enough
to be a regression fixture, not permission to bypass production gates.

### False promotion alert

- The 11:06 alert named run `29944744744`, the previously accepted run.
- The 28 July promoter invocation exited zero with `outcome=no_change`.
- Skipped recent-shadow checks had still chained into the promoter, which
  revalidated the existing roughly 341 MB candidate each day.
- `timetable_promotion_check()` independently opens an issue when the promoter
  wrapper's last success/skip is more than 30 hours old.
- Today's failed shadow correctly prevented promotion, so that age was expected.
- Alert formatting used the older `no_change` detail; it had no failure code or
  error, hence `unknown_failure`.

### Pi capacity and security baseline

Measured during investigation:

- about 904 MiB RAM total, 422 MiB available at the sample;
- about 904 MiB swap, 152 MiB used;
- current service p95: site 169.8 MiB, collector 68.4 MiB, bot 131.2 MiB,
  tunnel 35.1 MiB;
- no guessed `MemoryMax` is installed on the timetable oneshots;
- unprivileged shadow unit has `NoNewPrivileges`, strict filesystem access and
  no capabilities;
- root promoter has only `CAP_CHOWN`, `CAP_DAC_OVERRIDE` and `CAP_FOWNER`, a
  fixed candidate/live path and strict filesystem access.

Preserve this split. The Pi should compare candidates, not rebuild them. Avoid
multiple expensive full-database joins and measure peak RAM/swap during a
promotion-disabled shadow before introducing a memory limit.

Local temporary evidence may still exist at:

- `C:\tmp\bbb-run-29944744744`
- `C:\tmp\bbb-run-30421182234`
- `C:\tmp\compare_timetable_candidates.py`

These are disposable and must not be committed. GitHub artifacts expire after
seven days; download exact artifacts again if those folders are absent or their
hashes cannot be verified.

## Non-negotiable invariants

Keep all of the following:

1. GitHub builds only from the fixed workflow on the default branch and cannot
   reach the Pi.
2. The Pi independently verifies repository, event, branch, commit, workflow,
   artifact digest, three-file allowlist, manifest, attribution and database
   hash.
3. Download/extraction/build work is unprivileged and cannot write the live
   database or restart consumers.
4. The root promoter accepts fixed paths only, revalidates before and after copy,
   uses atomic replacement and retains `timetable.db.previous`.
5. Collector, site, stop-search, bot and public health gate acceptance; any
   post-replacement failure rolls back and proves recovery.
6. Backup, timetable and other heavy jobs share the Pi lock. Timer spacing is
   not correctness.
7. A failed or doubtful candidate never becomes permission to promote.
8. Slack and logs contain allowlisted fields, not tokens, host identity, raw
   downloaded strings or uncontrolled exception text.
9. No broad `--force`. If a future override is ever implemented, it must be
   root-owned, short-lived and pinned to an exact run and SHA. It may waive only
   a relative plausibility gate, never provenance, schema, hash, health or
   rollback.

## Implementation plan

### 1. Add a bounded semantic service profile

Add a shared, testable module used by shadow and promoter. Open both databases
read-only and immutable where supported, enable `query_only`, bound the SQLite
cache, use file-backed temporary storage and fixed SQL. Set an internal deadline
inside the systemd runtime ceiling.

Scan each database once to aggregate the facts needed by:

- Bristol-local service date;
- operator/NOC;
- route inside operator;
- trips;
- stop times;
- active service IDs, including `calendar` and `calendar_dates` additions and
  removals.

Derive the 28-day acceptance window and at least 180 days of forward coverage
from those aggregates in Python. Do not execute one large join per date or per
operator. Record query duration and peak transaction duration.

### 2. Use one versioned acceptance policy

Start with conservative values, then confirm them against fixtures before merge:

| Gate | Initial policy |
|---|---:|
| Every near-term day's trips and stop times | at least 85% of live |
| Next 28 days total | at least 90% of live |
| Route coverage | at least 80% of live |
| Each substantial operator | at least 70% of live |
| Forward coverage | no cliff below 75% of live |

Define “substantial operator” from live dated volume, not a hard-coded company
list. Record the policy version and every current/candidate/minimum value.

Keep structural/inventory checks for routes, stops, route shapes, required First
routes, stop-route pairs, schema, indexes, integrity, journal mode, duplicates,
edition windows and geometry. Raw total trips/stop times remain diagnostics or
a deliberately low catastrophic floor. Do not merely change 0.75 to 0.70.

For a possible operator transfer, require lost dated service volume to reappear
under another NOC with compatible route/service shape. Treat it as a warning
for review. Never let an unrelated operator using the same public route number
mask a loss. A strike or genuinely large timetable cut may still be held for
human review; that is correct fail-closed behaviour.

### 3. Make state records diagnostic and backward compatible

Extend delivery/promotion records without breaking existing readers:

- schema version;
- attempt ID, started/finished time and duration;
- GitHub run, commit and database hash;
- outcome and named failure code;
- allowlisted numeric comparison details including metric/date/operator;
- last accepted identity stored separately from last attempted identity;
- rollback/recovery result.

`DeliveryError` should carry safe structured context separately from its
internal exception. Slack may render only the allowlisted context. The current
pre-candidate promotion failure path must not overwrite last-accepted metadata.

### 4. Treat shadow and promotion as one incident

Change aggregate health so promotion is expected only after a new successful
shadow. If shadow fails, report “promotion not attempted; existing timetable
retained”. Remove the independent 30-hour promoter-age alarm.

Correlate wrapper job record and detailed attempt by run/hash/timestamps. A
newer wrapper failure must never inherit an older successful detail. Recovery is
sent only when both shadow and promotion state are healthy or intentionally
idle.

Fingerprint each failed attempt by kind, run, time, code and hash. Send each new
attempt once even if another timetable issue is already open; do not resend it
every 15 minutes. Make `notify()` return success and update the notification
fingerprint only after Slack returns 2xx, so transient Slack failure retries.

Teach `pipeline/status_digest.py` to consume only aggregate health and include a
short timetable line: last accepted run/date, last attempt/outcome, next retry
or blocked-by-shadow. This provides twice-daily redundant visibility without a
second interpretation of raw job files.

### 5. Correct lock, skip and attended semantics

The units currently use `flock -E 75` while `run_recorded_job.py` treats 75 as a
benign skip. Give lock timeout a distinct non-skip exit such as 73 and record
`lock_timeout`. Keep 75 for expected application outcomes such as cooldown or
same accepted candidate.

Do not start the expensive promoter after a shadow that merely says “recent
successful delivery; nothing new”. A fast no-change decision must be keyed to
the last shadow run, accepted run and hash, not just filename presence.

The base shadow template has unconditional
`OnSuccess=bbb-timetable-promote@auto.service`; therefore an exact `@RUN_ID`
shadow can promote when the root marker is present. Correct the unit topology or
add a safe attended drop-in so an attended shadow is diagnostic until an
explicit attended promoter is invoked. Pin that promoter to the reviewed
run/hash. Do not depend only on a shared lock: there is a gap between units.

### 6. Strengthen promotion health without widening privilege

Keep the current collector verification, site `/healthz`, real
`/api/stops-with-locality`, public `/healthz`, service state, live hash and
rollback checks. The bot health gate must additionally require the nested
payload at `details.healthData.database.timetable.connected` to be true; outer
`success=true` and `runtime=systemd` alone are insufficient.

No extra network destination, writable path or Linux capability is needed for
this change.

## Required tests

Unit and integration fixtures must cover:

1. Exact 22/29 July pair: historical/superseded shrink passes.
2. One missing near-term day: fails with date and metric.
3. Material operator missing while another operator masks the total: fails.
4. Legitimate operator transfer with equivalent dated service: warning/pass.
5. Same route number but unrelated service: cannot mask loss.
6. Plenty of historical rows but no usable near-term service: fails.
7. Future coverage cliff: fails.
8. `calendar_dates` addition/removal, bank holidays, Bristol local date and DST.
9. Malformed, oversized, slow or locked database: bounded named failure.
10. Lock timeout differs from benign skip.
11. Consecutive distinct failures while an incident is open each alert once.
12. Slack 5xx/timeout retries; Slack 2xx deduplicates.
13. Wrapper/detail identity mismatch never produces an invented explanation.
14. Old state files migrate safely and preserve last accepted identity.
15. Failures before replace, after replace, at each restart/health stage and
    during rollback leave the expected live file and recovery record.
16. Systemd unit verification, sandbox assertions and exact-run no-auto-promote.

Run Windows pytest groups separately where duplicate test basenames collide.
Do not commit real artifacts, databases, secrets or fleet data.

## Safe rollout sequence

No arbitrary week-long observation is required, but every evidence step is.

1. Implement on a branch and open a draft PR.
2. Run focused tests, repository CI, secret scan, workflow policy checks,
   systemd verification and the exact artifact regression.
3. Merge only after the diff and all safety boundaries are reviewed.
4. Trigger a **fresh** GitHub build from the corrected default branch; do not
   promote the older artifact merely because the new comparator likes it.
5. On the Pi, stop the timer and disable automatic promotion. Confirm the live
   database hash and recent backup before installing layout/control-plane code.
6. Install via the supported layout/deploy path, with no timetable replacement.
   Verify units, marker state, services and public health.
7. Run an exact, promotion-disabled shadow of the fresh run. Inspect provenance,
   semantic comparison, state records, duration, `memory.peak`, available RAM
   and swap. Abort on material pressure or any ambiguous warning.
8. Explicitly run the attended, run/hash-pinned promoter. Automatic rollback
   remains armed.
9. Verify live hash and `timetable.db.previous`; collector matching/freshness;
   site health and real stop search; bot nested timetable connection; public
   health; detailed attempt/job state; Slack; aggregate health and digest.
10. Restore the root marker and timer. Exercise a harmless no-new-candidate run
    to prove the fast path and confirm it does not revalidate/promote the old
    candidate unnecessarily.
11. Record the accepted run, performance and result in these documents and the
    private operating manual.

Rollback for the control-plane change is the layout installer's previous-unit
restore. Rollback for a database replacement is `timetable.db.previous` plus
consumer restart and full health proof. If recovery cannot be proven, disable
the timer/marker and keep the last known-good database; do not improvise a
manual file copy.

## Likely files to change

- `deploy/timetable_delivery.py`
- `deploy/timetable_promote.py`
- `deploy/aggregate_health.py`
- `deploy/run_recorded_job.py`
- `deploy/systemd/bbb-timetable-shadow@.service`
- `deploy/systemd/bbb-timetable-promote@.service`
- `pipeline/status_digest.py`
- the focused tests under `deploy/tests/` and relevant pipeline tests
- public deployment/execution docs and the gitignored `docs/DOMAIN.md` after
  deployment is actually proven

Prefer a small shared semantic-comparison module rather than duplicating SQL in
delivery and promotion. Avoid unrelated refactoring in this safety change.

## Broader backlog after this incident

This is the order to reconsider, not authority to implement every item at once.

### 1. Finish data-estate automation

Follow `docs/plans/DATA_REFRESH_AUTOMATION.md`:

- make registration the canonical fleet identity and operator + fleet code the
  display/lookup key;
- resolve duplicated fleet codes and the lossy `ref.split("-")[-1]` blurb-scope
  collapse;
- move mutable enrichment to durable `/var/lib/bristolbusbot/enrichment` paths
  with bot/site path overrides and transitional dual reads;
- make fleet refresh fail closed on request failure, empty 200 responses and
  unexplained per-operator count collapse;
- refresh stop localities after timetable acceptance;
- keep geography/boundary editions human-approved with provenance;
- add one unified data-health inventory before automating description refresh;
- retain GitHub merge approval for facts/news and human gating for generated
  descriptions.

### 2. Continue social expansion V2

Already on `main`:

- exact successful-post provenance, current-journey map badge and vehicle-profile
  mentions;
- logging-only Threads candidate selector;
- deterministic dark Instagram cards using the site's fonts and design system;
- one standalone bot quote plus a six-slide weekly carousel: headline, WECA
  target gap, daily results, delay distribution, electric versus diesel/other,
  and operator comparison;
- operator naming, captions, per-slide alt text and review manifests.

Still to do:

- verify the live badge/profile behaviour after any relevant deploy;
- run the Threads selector to at least 50 decisions across one complete service
  day, then choose thresholds from the actual feed;
- build the isolated `bbb-social` service and its own database only if Threads
  publishing proceeds; killing it must leave Bluesky and the core estate intact;
- keep Instagram posting manual during the pilot; make local output easy to
  upload and continue producing short captions and copyable alt text;
- decide after the pilot whether Meta app/OAuth/token/publishing work is worth
  doing. If yes, use idempotent delivery, unknown-state reconciliation and
  GitHub-merge approval. Do not add Meta credentials before that decision;
- do not add a second Gemini call or BODS consumer for either platform.

### 3. Editorial/reference maintenance

- periodically re-verify fiscal and transport facts against authoritative
  sources;
- maintain seasonal occasions and the official-news discovery/approval flow;
- keep topical facts integrated into the bot's voice rather than as dry
  addenda, while preserving deterministic requirement gates and the separate
  factual verifier;
- keep source URLs as private provenance, not appended to public posts.

### 4. Optional infrastructure decisions

- A community member offered a VM and Google Cloud remains available. No
  migration has been chosen. The Pi + GitHub split currently fits the workload;
  reconsider external hosting only for a concrete resilience/capacity need.
- Preserve live plus `.previous` on the Pi, encrypted restic on the USB drive
  and the off-device copy. GitHub delivery artifacts are not backups.
- Continue attended Pi OS/package maintenance; never combine unattended data
  automation with broad system upgrades or destructive cleanup.

### 5. Longer-tail product ideas

- route-under-the-microscope social format after sample gates;
- depot allocation visualisation;
- a read-only public API;
- SIRI-SX disruption posts only after a verifiable source/corroboration contract.

Recent live-site sidebar/design, audit geography and route/stop/vehicle
visualisation work is considered landed. Re-open it only for a reproduced bug
or a new product decision, not as part of timetable remediation.

## Completion statement for the next chat

Do not say “timetable automation is fixed” until a fresh corrected artifact has
passed the promotion-disabled Pi shadow, attended promotion, all consumer and
functional health gates, Slack/aggregate/digest evidence, and a harmless
unattended follow-up. Until then the accurate statement is: **production is safe
on the previously accepted timetable; the first due refresh exposed a false
negative validator and a false positive alert, both with a fully specified
repair plan.**
