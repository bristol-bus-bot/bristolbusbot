# Architecture

Bristol Bus Bot is one ecosystem with a single data spine: one process
polls the Bus Open Data Service (BODS), matches vehicles to the timetable
and computes delays once; everything else — the live website, the
punctuality audit and the Bluesky bot — reads what it wrote. The site
displays a delay, the audit records it and the bot posts about it, and
those three numbers cannot disagree because they are the same number.

```
 BODS SIRI-VM ──┐
 BODS SIRI-SX ──┤            ┌── site (Flask) ──────▶ bristolbuses.live
                ▼            │
        collector ──▶ live.db┼── bot (TS) ───────────▶ Bluesky
                ──▶ audit.db ┴── nightly rollup ─────▶ GitHub Pages (audit)
                ▲
        timetable.db  ◀── pipeline (monthly validated build)
```

## Components

| Folder | What it is | Language | Runs where |
|---|---|---|---|
| `collector/` | The only process that talks to BODS. Polls SIRI-VM (positions) and SIRI-SX (disruptions) every 30 s, filters to the WECA boundary by point-in-polygon, matches vehicles to the timetable, computes delays. | Python | Raspberry Pi (systemd) |
| `site/` | bristolbuses.live — live map and dot-matrix departure board (Flask + Leaflet). Read-only consumer of the collector's output. | Python/JS | Raspberry Pi (gunicorn/systemd) |
| `bot/` | The Bluesky bot. Reads the collector's event stream, selects an event, generates AI commentary with a persona, posts. | TypeScript | Raspberry Pi (systemd) |
| `pipeline/` | Offline data jobs: the 3-layer timetable build, fleet refresh, route shapes, audit rollup/export. | Python | Workstation + Pi timers |
| `audit-site/` | Source of the WECA bus punctuality audit static site, published to a separate GitHub Pages repository. | HTML/CSS/JS | GitHub Pages |
| `deploy/` | Immutable release deployment, systemd units, health/rollback gates, backups. | Python | Workstation → Pi |

## Databases and ownership

SQLite everywhere, with strict ownership boundaries:

| File | Writer | Readers | Contents |
|---|---|---|---|
| `timetable.db` | pipeline (built on the workstation, deployed atomically) | collector, site, bot | GTFS + TransXChange + TNDS schedule data; read-only in production |
| `live.db` | collector | site, bot | current vehicle state, disruptions (`situations`), corroborated delay `events` for the bot, poller status |
| `audit.db` | collector + nightly rollup | rollup/export jobs | closest-approach timing-point observations and daily summaries |
| `app_data.db` | bot | bot | posting history, engagement analytics, bot-local state |

The bot's only write to `live.db` is marking events consumed
(`consumed_by_bot_at`); it never deletes rows. The site writes nothing.

## The collector

The full behavioural specification is `docs/plans/COLLECTOR_SPEC.md`.
The short version:

- SIRI-VM every 30 s, SIRI-SX (disruptions) every 5 minutes. One shared
  HTTP session with retry/backoff; a failed poll never clears state —
  staleness is computed at read time.
- Matching is tiered and fail-closed: exact journey reference first, then
  a fuzzy match on operator + line + direction + first-stop departure
  window + calendar pattern, with a position gate (a candidate schedule
  must have a stop within 3 km of the vehicle). No confident match means
  no delay at all, never a guess.
- Two delay methods for two purposes: a per-poll closest-stop **live
  estimate** for the map, and **settled readings** at timing points
  (closest approach, 150 m gate) which are the audit's published basis.
- Delays are observed, never smoothed, predicted or blended. Stability
  comes from corroboration: an event is only written for the bot after
  consecutive polls agree.
- Stale re-broadcast positions (BODS re-emits parked vehicles with old
  `RecordedAtTime`) are discarded at ingest; the site independently hides
  the same signature.
- Delays are stored in seconds; consumers round at display time.

## The site

Flask + gunicorn on loopback, published only through a named Cloudflare
tunnel. It contains no BODS-fetching code at all. Fonts and Leaflet are
self-hosted; the only third-party browser dependency is Carto map-image
tiles (documented in `site/README.md`). Frontend assets are served as a
content-addressed graph under `/assets/<hash>/…` so a deployment can
never be half-cached. Dynamic DOM rendering goes through a safe element
helper rather than `innerHTML`.

## The bot

The bot keeps its brain (persona, commentary, posting judgement, rate
limits) and has no plumbing: its production input is the collector's
`events` table (`bot/src/ingest/event-reader.ts`). Its control API binds
to loopback only and control endpoints require a bearer token. The
legacy direct-SIRI ingest path still exists behind `INGEST_MODE=siri` as
an explicit diagnostic fallback, never a default.

## The audit

A nightly, networkless rollup turns `audit.db` into publishable daily
summaries; a separate networked job pushes the data file to the audit
site's GitHub Pages repository. The integration snapshot the live site
reads (headline percentage, vehicle profiles, rare-working evidence) is
promoted only after that push succeeds, so the live site can never cite a
publication run that failed. If the snapshot is more than 48 hours old
the site hides the audit headline and profile pages entirely — stale
data degrades to absence, not to a broken or misleading figure.
Methodology and its limitations: `docs/AUDIT_METHODOLOGY.md`.

## Deployment shape

Production runs from immutable, hash-manifested releases selected by an
atomic `current/<component>` symlink, with per-component health gates and
automatic rollback. Configuration and secrets live outside releases;
durable state lives in a dedicated state directory. See
`docs/DEPLOYMENT.md`.

## Invariants

These hold everywhere and changes must not break them:

1. One poller. Nothing but the collector talks to BODS.
2. One delay number: observed, unblended, in seconds, computed once.
3. Drop, don't guess: unconfident matches are discarded and counted.
4. The audit's published figures use timing points and the DfT on-time
   band (−60 s to +359 s) only.
5. The public site is a read-only consumer and binds to loopback; only
   the named tunnel publishes it.
6. The bot API is loopback-only and authenticated; delay values in posts
   are collector observations, never bot-side predictions.
7. Timetable builds are fail-closed: a corrupt, stale, route-incomplete
   or shape-incomplete database is refused, never deployed.
8. Secrets live in environment files on the production host, never in
   the repository or in releases.
