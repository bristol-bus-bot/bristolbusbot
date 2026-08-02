# Roadmap

What is done, what is in motion and what is planned. Dates are when the
work actually landed; nothing here is a promise.

## Current state (July 2026)

The core system is live:

- Shared collector, live site, audit and bot all in production under
  systemd, launched publicly at bristolbuses.live on 13 July 2026.
- Immutable release deployment with health gates and automatic rollback
  (`deploy/push.py`) is the only production deployment path.
- End-to-end timetable automation: the Pi detects when a refresh is due,
  GitHub builds it, and the Pi validates, promotes or rolls back. The complete
  production `auto` path passed on 22 July 2026 and the laptop is no longer part
  of routine timetable production. The first timer-due build on 29 July was
  safely rejected before promotion: its near-term service was complete, but a
  flat total-row gate counted superseded editions. The accepted timetable stayed
  live. Service-window validation and correlated alerting are the current
  hardening task.
- Encrypted local and off-site backups, weekly repository checks, restore
  verification tooling and independent dead-man monitoring.
- Self-hosted fonts and Leaflet; content-addressed frontend assets;
  route-first search with mobile route view.
- Live-site audit integration: a sample-gated headline statistic linking
  to the full audit, and aggregate per-vehicle profile pages behind
  stable opaque slugs. Both hide themselves when the published snapshot
  is stale rather than showing old numbers.
- Rare-working detection runs in posting-disabled shadow mode. It stays
  silent by design until 56 complete prior service days of baseline
  exist; that silence is healthy, not a bug.
- Sourced bot facts and special dates are data rather than prompt code. Official
  bus-news discovery opens a GitHub approval PR; merged content is bounded by
  expiry/cooldowns and has a validated, health-gated Pi delivery path.
- Exact successful-post provenance now links a bot post to the same live journey
  on the map and to durable vehicle-profile mentions. Missing or stale bot data
  simply removes the decoration.
- Review-only social tooling now selects Threads candidates without publishing
  and renders a standalone bot quote plus a six-slide, operator-labelled weekly
  Instagram carousel. Captions, alt text and source facts travel with the pack;
  no Meta credential or automatic publisher exists.

## Planned

The active execution order is maintained in
`docs/plans/ENRICHMENT_UI_SOCIAL_EXECUTION_INDEX.md`. The capabilities below
remain the roadmap, but the isolated manual-Instagram curation path now comes
before Threads; it does not waive any timetable or data-safety gate.

In rough order, each gated on the one before where it matters:

1. **Correct the timetable acceptance and alerting defects found on 29 July.**
   Compare usable service by date/operator/route rather than raw historical row
   bulk; correlate shadow and promotion as one incident; distinguish lock
   timeout from a harmless skip; pin attended promotion to the reviewed run;
   and expose the result in the daily digest. Prove it with the exact failed
   artifact pair, hostile fixtures and a promotion-disabled Pi shadow before a
   fresh attended promotion. Full handover:
   `docs/handovers/2026-07-29_TIMETABLE_AUTOMATION_HANDOVER.md`.
2. **Continue the remaining data-estate automation.** Next come operator-safe
   vehicle identity and durable consumer paths, decoupling generated data from
   code releases, a unified data-health audit, then fail-closed fleet/locality
   refreshes and human-gated description generation. The authoritative sequence
   is `docs/plans/DATA_REFRESH_AUTOMATION.md`.
3. **Isolated Slack-to-card curation.** A separate process reads an allowlisted
   private Slack channel, verifies a shared Bluesky link against exact stored
   provenance, and returns a deterministic Instagram card for manual posting.
   Social failures must be unable to affect the collector, site, audit
   or Bluesky — killing the social service leaves everything else
   healthy. No deployment target exists until the service is implemented.
4. **Continue the Instagram manual pilot.** Branded data cards are generated
   from stored post provenance and the audit archive, delivered to the phone,
   and posted manually. Numbers and quoted text are deterministic; Slack text
   never becomes card content. Full execution checklist:
   `docs/plans/ENRICHMENT_UI_SOCIAL_EXECUTION_INDEX.md`.
5. **Threads as a curated mirror.** Reuses the exact final Bluesky text
   (no second AI call, no second BODS consumer), selected by a significance
   budget with route cooldowns and a hard ceiling of 15 posts per day. Runs
   logging-only for at least one complete service day and 50 decisions before
   thresholds are chosen and publishing is built.
6. **Longer tail** (unordered): depot
   allocation visualisation, an open read-only API, SIRI-SX disruption
   posts once a verifiable source/corroboration contract exists for
   them.

## Deliberate exclusions

No Postgres, no Docker, no frontend frameworks, no multi-city ambitions.
SQLite, systemd and native ES modules are the right size for this
project, and Bristol is the point.
