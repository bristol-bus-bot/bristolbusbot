# Enrichment, UI and social curation execution index

Status: active execution checklist, 3 August 2026.

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

Status: hardening is in draft PR #30 and the repository permission is enabled;
the attended main-branch workflow test waits for review and merge.

- [x] Confirm the exact failure: GitHub Actions is blocked from creating PRs.
- [x] Replace per-run branch names with one stable branch per story.
- [x] Remove the redundant search-query duplicate check.
- [x] Preserve the curated JSON layout so one story produces a small diff.
- [x] Add a useful job summary when PR creation fails.
- [x] Validate the real GOV.UK story against a scratch context file.
- [x] Verify and delete the eight abandoned failed-run branches.
- [x] Open draft PR #30 through the normal reviewed GitHub path.
- [x] Enable and verify the repository Actions PR-creation permission.
- [ ] Review and merge PR #30.
- [ ] Run one attended `workflow_dispatch` that actually reaches PR creation.
- [ ] Review the resulting editorial PR on its wording and facts.

Do not call this fixed merely because the qualifying story ages out.

### 1. Header status filters

Status: implemented and verified locally; not deployed.

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

### 2. Slack-to-Instagram-card curation

Status: local renderer and curation core implemented and tested; ARM64, Slack
credentials and any real Slack contact remain attended gates.

- [x] Add and locally smoke-test a single-card renderer mode.
- [ ] Run the renderer smoke test on ARM64.
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
- [ ] Run locally, then ARM64, then shadow, then one attended real Slack upload.
- [ ] Keep Instagram posting manual.

The later engagement shortlist is optional and cannot block link-driven cards.

### 3. Enrichment automation

Status: resumes behind the existing timetable proof and data-safety gates.

- [ ] Complete the promotion-disabled timetable Phase A proof.
- [ ] Fix operator-safe vehicle identity before changing keyed artifacts.
- [ ] Add durable consumer-path overrides for every bot artifact.
- [ ] Use a small shared promotion library plus fixed artifact-specific
  wrappers; do not create an arbitrary configurable root promoter.
- [ ] Run the data-health audit report-only for two weeks.
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
