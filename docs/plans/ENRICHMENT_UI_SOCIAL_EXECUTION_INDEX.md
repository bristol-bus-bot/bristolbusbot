# Enrichment, UI and social curation execution index

Status: active execution checklist, 4 August 2026.

This is the short, authoritative order of work. The much larger
`ENRICHMENT_UI_AND_SOCIAL_CURATION_PLAN.md` remains the design record and
technical rationale; it is not one giant implementation ticket.

## Rules that remain binding

- Old valid production data beats new doubtful data.
- Data generators write candidates, never live paths.
- Validation, promotion, health checking and rollback are separate gates.
- No new BODS consumer and no second live poller.
- Slack and Instagram work can fail without affecting the collector, site,
  bot or tunnel.
- Instagram publishing remains a manual phone action.
- Published audit figures are never silently rewritten.
- Rust is parked until the Python/TypeScript system is stable and measured.
- Do not deploy two unrelated workstreams in the same attended window.

## Current order and status

### 0. Editorial-news workflow incident

Status: fixed and proved on `main`. PR #30 was merged, the attended workflow
created PR #31 successfully, and the maintainer rejected that story by closing
the PR without merge. Its generated branch was removed.

- [x] Confirm the exact failure: GitHub Actions is blocked from creating PRs.
- [x] Replace per-run branch names with one stable branch per story.
- [x] Remove the redundant search-query duplicate check.
- [x] Preserve the curated JSON layout so one story produces a small diff.
- [x] Add a useful job summary when PR creation fails.
- [x] Validate the real GOV.UK story against a scratch context file.
- [x] Verify and delete the eight abandoned failed-run branches.
- [x] Open draft PR #30 through the normal reviewed GitHub path.
- [x] Enable and verify the repository Actions PR-creation permission.
- [x] Review and merge PR #30.
- [x] Run one attended `workflow_dispatch` that actually reaches PR creation.
- [x] Review the resulting editorial PR on its wording and facts; reject PR #31.

Do not call this fixed merely because the qualifying story ages out.

### 1. Header status filters

Status: deployed as site release `20260803t001525357513z-ccda69b1` and
verified on the live desktop and phone-width layouts. Local and public health
remained green.

- [x] Add one exclusive status classifier with precedence:
  `depot -> waiting -> delayed/early/punctual`.
- [x] Derive both header counts and filter matching from that classifier.
- [x] Convert the five status chips to accessible toggle buttons.
- [x] Reuse the existing hollow-marker emphasis, including depot markers.
- [x] Keep route and status emphasis mutually exclusive in v1.
- [x] Preserve an already-open vehicle information panel when filtering.
- [x] Add classifier, interaction, cache-key and keyboard tests.
- [x] Verify desktop and phone layouts locally. Production deployment remains
  a separate attended gate.
- [x] Deploy only the site component and verify the live filter interaction,
  four core services, failed-unit list and local/public health.

### 2. Slack-to-Instagram-card curation

Status: commissioned on 4 August 2026. PRs #32, #35, #36, #37 and #38 are
merged. Social release `20260804t205910782323z-b4980e79` and the reviewed
layout are installed on the Pi. The private Slack app, allowlisted channel/user
and root-only credential are configured. Checkpoint seeding, a 1080 x 1350
shadow render and one attended live delivery all passed. Slack's read-back
confirmed the JPEG, alt-text reply and caption reply in the correct private
thread. The live marker is present and the three-minute timer is enabled and
active. Its first automatic firing completed successfully without creating a
duplicate request, file or reply. Instagram posting remains manual.

- [x] Add and locally smoke-test a single-card renderer mode.
- [x] Run the renderer smoke test on ARM64: native dependencies installed and
  produced a 1080 x 1350 JPEG; the temporary Pi directory was removed.
- [x] Accept links only from the allowlisted Slack user and private channel.
- [x] Resolve the Bluesky actor to its DID and compare the full AT URI.
- [x] Verify that the public post still exists; fail closed if Bluesky cannot
  answer, so a deleted post cannot be rendered accidentally.
- [x] Treat Slack text only as a lookup request. Render only stored provenance.
- [x] Store delivery attempts, Slack message/file IDs and template version in
  `social.db`; call this a delivery ledger, not an Instagram posting log.
- [x] Make retries reconcile before upload. The implementation uses Slack file
  reads, so request `files:read` explicitly as well as `files:write`.
- [x] Provide an attended `--new-version` path for intentional regeneration.
- [x] Make the first poll seed its checkpoint at the current time instead of
  replaying retained Slack history.
- [x] Add an explicit `social` release, root-owned configuration helper,
  credential-gated sandboxed service/timer, shadow-default runner, live marker,
  aggregate health, digest and deployment documentation.
- [x] Merge PR #32 and deploy the social release and layout with the timer
  disabled; verify local/public health and all four core services.
- [x] Configure the private channel/user IDs and root-only bot token on the Pi;
  validate the hidden values, confirm the bot is a channel member, and leave
  the timer disabled and live marker absent.
- [x] Merge PR #35 and deploy social release
  `20260804t003554677599z-c54b1602`; the systemd-credential compatibility gate
  then passed on the Pi.
- [x] Merge PR #36 as `cc8c363d`, install the reviewed layout and move the
  SQLite ledger into `/var/lib/bristolbusbot/social/social.db`, where its WAL
  sidecars are inside the service's dedicated writable state directory.
- [x] Reinstall the Slack app with the five documented bot scopes, replace the
  Pi credential through the hidden configuration helper, validate it, and run
  the first successful shadow poll. It seeded the private-channel checkpoint;
  the ledger contained zero requests and zero deliveries, the timer remained
  disabled and the live marker remained absent.
- [x] Merge PR #37 as `e0716f09`, deploy social release
  `20260804t204401034353z-e0716f09` and repeat the phone-friendly share. The
  parser read Slack's actual link target once while still refusing two
  separately supplied links. Shadow mode rendered one 1080 x 1350 JPEG with
  the verified text, caption and alt text; it sent no reply or upload.
- [x] Merge PR #38 as `b4980e79`, deploy social release
  `20260804t205910782323z-b4980e79` and repeat the attended live delivery. The
  form-encoded upload flow delivered one JPEG plus acknowledged alt-text and
  caption replies. A Slack API read-back found all three in the correct private
  thread. The ledger recorded one `delivered` request with no error; the
  three-minute timer was then enabled and became active. Its first automatic
  firing succeeded and left the request and delivery counts unchanged.
- [x] Keep Instagram posting manual.

The later engagement shortlist is optional and cannot block link-driven cards.

### 3. Enrichment automation

Status: incident resolved on 4 August 2026. The scheduled rollup failed at
05:15 on 3 August because pipeline release
`20260730t180316535210z-56fd00a3` omitted required `fbribuses.json`; aggregate
health then reported `job:audit-rollup`. This predated and was independent of
the social deployment.

- [x] Identify the broken ownership contract: a clean release correctly
  omitted the private generated artifact, while the rollup still depended on a
  mutable copy inside another component's release.
- [x] Merge PR #34 as `f7e83307`; it establishes
  `/var/lib/bristolbusbot/enrichment/fbribuses.json`, fixes the rollup to that
  path, and adds parse gates without packaging private data.
- [x] Atomically bootstrap the durable file from the validated live bot copy
  (2,605 vehicles, 4,386 lookup entries; SHA-256
  `69b953091c942005908546f2e30a74656100fc4666f5b32820a428868b7be976`),
  deploy pipeline release `20260804t001717496979z-f7e83307`, catch up and
  publish the missing 2 August rollup, and prove aggregate health is `ok` with
  no issues or failed units.
- [x] Complete the promotion-disabled timetable Phase A proof. Corrected run
  `30568434088` passed the attended diagnostic shadow and separate exact-hash
  promotion on 30 July; the previous database remained available, aggregate
  health is `ok`, and automatic no-change checks stayed harmless through
  5 August.
- [x] Fix operator-safe vehicle identity before changing keyed artifacts.
  Read-only production evidence on 5 August found 19 wrong legacy fleet-record
  matches and 26 ambiguous description identities among 995 recently observed
  identities. PR #50 (`77e54c02`) deployed registration-first operator-scoped
  readers, safe legacy fallback, schema-2 blurb scope and collision fixtures.
  Exact live checks proved the previously cross-wired examples now resolve to
  the correct operators, while ambiguous enrichment returns nothing.
- [ ] Add durable consumer-path overrides for every bot artifact.
- [ ] Use a small shared promotion library plus fixed artifact-specific
  wrappers; do not create an arbitrary configurable root promoter.
- [ ] Run the data-health audit report-only until three consecutive clean daily
  reports and an injected operator-count-collapse warning have been reviewed.
- [ ] Refactor fleet refresh to all-or-nothing candidates and shadow it.
- [ ] Automate locality derivation as specified by
  `DATA_REFRESH_AUTOMATION.md`; it is not silently dropped from scope.
- [ ] Add a durable atomic Gemini usage ledger and pending human approval.
- [ ] Treat Pi files as production authority and repository copies as
  provenance snapshots that deploys cannot restore over live data.
- [ ] Document source identification, pacing, refusal/kill controls and
  unattended-use policy before scheduling bustimes.org access.

### 4. Measurement quality

Status: read-only reporting may start early; collector writes wait for the
timetable protection proof.

- [ ] Key an observed trip by service date, operator, journey and scheduled
  start so repeated services are not conflated.
- [ ] Treat extreme tails, progression failures and bimodality as investigation
  flags, not automatic proof that data is wrong.
- [ ] Compare route distributions by operator, direction and time band.
- [ ] Add bounded, allowlisted evidence fields with compression and retention.
- [ ] Measure evidence/archive volume before choosing a retention period.
- [ ] Keep new exclusions documented, counted and effective-dated in the audit
  methodology.

### 5. Rust consolidation

Status: parked RFC only.

No migration work starts until workstreams 0-4 are stable and measurements show
a concrete benefit. Replay fixtures, resource measurements and watchdog work
may be useful independently, but they do not imply approval for a rewrite.

## Document authority

- This file controls workstream order and current execution status.
- `DATA_REFRESH_AUTOMATION.md` controls data safety and promotion policy.
- `SOCIAL_EXPANSION_PLAN_V2.md` controls editorial rules, budgets and kill
  criteria; this index supersedes only its rollout ordering.
- `AUDIT_METHODOLOGY.md` controls published measurement meaning.
- `ENRICHMENT_UI_AND_SOCIAL_CURATION_PLAN.md` supplies detailed rationale.
