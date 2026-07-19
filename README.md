# bristolbusbot

This is one ecosystem for live bus tracking, punctuality auditing, and social-media
commentary across the West of England (WECA), built entirely on open data
from the Bus Open Data Service (BODS). This started out as a hobby engineering project when I got a Raspberry Pi 4.
After many damp waits for rush hour buses in East Bristol, I decided to weaponise my frustration and automate being passive-aggressive about First Bus, so here we are.

## The map

| Folder | What it is | Language | Runs where |
|---|---|---|---|
| `collector/` | The only process that talks to BODS. Polls SIRI-VM (positions) and SIRI-SX (disruptions) every 30s, matches vehicles to the timetable, computes delays once for everyone. | Python | Raspberry Pi (systemd) |
| `site/` | **bristolbuses.live** - live map + dot-matrix departure board (Flask + Leaflet). | Python/JS | Raspberry Pi (gunicorn/systemd) |
| `bot/` | **@bristolbusbot.live** - Bluesky bot. AI commentary on delays, with persona. Reads the collector's event stream. | TypeScript | Raspberry Pi (systemd) |
| `pipeline/` | Offline data jobs: 3-layer timetable build (BODS GTFS → operator TransXChange → TNDS), fleet data refresh, AI blurb generation, route shapes. | Python | Workstation |
| `audit-site/` | Source of the **WECA bus punctuality audit** static site, published via GitHub Pages (nightly data push from the Pi). | HTML/CSS/JS | GitHub Pages |
| `deploy/` | Immutable release deployment, systemd units, health/rollback gates, backups, Slack monitoring and named-tunnel configuration. | Python | Workstation → Pi |
| `docs/` | Architecture, decisions, deployment, roadmap and audit methodology. | — | — |

## Architecture

```
 BODS SIRI-VM ──┐
 BODS SIRI-SX ──┤            ┌── site (Flask) ──────▶ bristolbuses.live
                ▼            │
        collector ──▶ live.db┼── bot (TS) ───────────▶ Bluesky
                ──▶ audit.db ┴── nightly rollup ─────▶ GitHub Pages (audit)
                ▲
        timetable.db  ◀── pipeline (monthly validated build)
```

There is one poller, one matcher and one delay number - the site displays it, the audit
records it, and the bot posts about it. These processes cannot disagree by construction.

These are the principles that shape the code:

- Delays are measured against the timetable
  from reported positions so nothing is smoothed, predicted or interpolated
  in any number that gets recorded or posted.
- If a vehicle can't be confidently matched to a
  scheduled trip (operator + line + direction + departure window + route
  proximity), it gets no delay at all rather than a wrong one.
- It uses timing points only, to the DfT definition, for the audit's
  punctuality statistics (−1 to +5:59 around the scheduled time).

## Running locally

- **collector**: `cd collector && pip install -e . && python -m collector.run`
  — needs `.env` with `BODS_API_KEY`, plus a `timetable.db` built by the
  pipeline (set `BBB_TIMETABLE_DB` to its path).
- **site**: `cd site && pip install -e . -e ../collector && python wsgi.py`
  → http://localhost:5000. Point `BBB_LIVE_DB` at the collector's live.db.
- **bot**: `cd bot && npm ci && npm run typecheck && npm run build`
  (local `.env` must keep test mode enabled). See `bot/.env.example`.
- **pipeline**: run `python deploy\push.py --refresh-timetable` from the
  repository root to build, validate and atomically deploy the timetable and
  route shapes. Use `python deploy\push.py --dry-run --refresh-timetable` to
  inspect its scope without building, connecting or changing anything.

Production code is deployed only through `python deploy\push.py`; see
`deploy/README.md` for the exact scope of every command and
`docs/DEPLOYMENT.md` for the overall production shape.

## Data sources & licence

Timetables, vehicle locations and disruptions: BODS (OGL v3.0). Fallback
timetables: Traveline National Dataset. Boundaries: ONS Open Geography
(OGL). Stops: NaPTAN (OGL). Fleet data is fetched at runtime from the
community-maintained bustimes.org API and is not redistributed in this
repository. Full attributions are in `ATTRIBUTION.md`.

Except where noted, code is licensed under AGPL-3.0-only. The adapted
TransXChange parser remains under MPL-2.0; see `THIRD_PARTY_NOTICES.md`.
Not affiliated with, endorsed by, or funded by any bus operator, WECA, or any
authority.
