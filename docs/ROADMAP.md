# Roadmap

What is done, what is in motion and what is planned. Dates are when the
work actually landed; nothing here is a promise.

## Current state (July 2026)

The core system is complete and live:

- Shared collector, live site, audit and bot all in production under
  systemd, launched publicly at bristolbuses.live on 13 July 2026.
- Immutable release deployment with health gates and automatic rollback
  (`deploy/push.py`) is the only production deployment path.
- End-to-end timetable automation: the Pi detects when a refresh is due,
  GitHub builds it, and the Pi validates, promotes or rolls back. The first
  fully unattended live promotion passed on 22 July 2026, so the laptop is no
  longer part of routine timetable production.
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

## Planned

In rough order, each gated on the one before where it matters:

1. **Finish the remaining data-estate automation.** Timetable delivery is
   complete. Next come operator-safe vehicle identity and durable consumer
   paths, decoupling generated data from code releases, a unified data-health
   audit, then fail-closed fleet/locality refreshes and human-gated description
   generation. The authoritative sequence is
   `docs/plans/DATA_REFRESH_AUTOMATION.md`.
2. **Isolated social service.** A separate process with its own database
   receives a best-effort handoff after each successful Bluesky post.
   Social failures must be unable to affect the collector, site, audit
   or Bluesky — killing the social service leaves everything else
   healthy. No deployment target exists until the service is implemented.
3. **Threads as a curated mirror.** Reuses the exact final Bluesky text
   (no second AI call, no second BODS consumer), capped at one qualifying
   post per rolling hour with significance and cooldown gates. Runs
   logging-only in shadow before going live.
4. **Instagram as a visual editorial product.** Branded data cards
   generated from the audit archive, with every post human-approved for
   the first 30 days. Numbers are deterministic; AI garnishes, never
   generates figures. Full editorial and technical specification:
   `docs/plans/SOCIAL_EXPANSION_PLAN.md`.
5. **Longer tail** (unordered): depot
   allocation visualisation, an open read-only API, SIRI-SX disruption
   posts once a verifiable source/corroboration contract exists for
   them.

## Deliberate exclusions

No Postgres, no Docker, no frontend frameworks, no multi-city ambitions.
SQLite, systemd and native ES modules are the right size for this
project, and Bristol is the point.
