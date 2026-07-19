# Production deployment

Run commands from the repository root in PowerShell. Production is managed by
systemd and `deploy/push.py` is the supported deployment entry point.

SSH key authentication and a manually verified host key for the Pi must
already exist. The deployer sets `StrictHostKeyChecking=yes` and never trusts a
new or changed host automatically.

Commit reviewed changes before a live command. A real deployment refuses a
dirty working tree so every release label and manifest maps to one Git commit.

## Workstation-only production identity

Copy `deploy/local.env.example` to `deploy/local.env` once, then replace its
deliberately fictional Darkplace values with the production SSH account,
hostname, home directory, backup filesystem UUID, Cloudflare tunnel UUID and
optional local GTFS directory. `deploy/local.env` is Git-ignored and must never
be committed. These identifiers are not passwords, but keeping them local
avoids publishing details of the maintainer's workstation and home server.

`push.py` reads that file automatically. The committed systemd, sudoers,
tmpfiles and tunnel files are templates; `--install-layout` and tunnel deploys
render private values only into temporary upload payloads.

## Commands and exact scope

| Command | Updates | Restarts | Does not touch |
|---|---|---|---|
| `python deploy/push.py --component collector` | collector and its monitoring scripts | collector | site, bot, tunnel, timetable, secrets |
| `python deploy/push.py --component site` | Flask site, static assets and its collector-library snapshot | site | collector process, bot, tunnel, state, secrets |
| `python deploy/push.py --component bot` | locally built bot, Node dependencies and runtime JSON | bot | collector, site, tunnel, state, secrets |
| `python deploy/push.py --component pipeline` | scheduled audit job code and reviewed audit-site assets | none | timetable, live services, secrets |
| `python deploy/push.py --component tunnel` | non-secret named-tunnel ingress config | tunnel | credential JSON and application code |
| `python deploy/push.py --component social` | planned social component (not implemented) | none; exits without changes | everything |
| `python deploy/push.py --all` | pipeline, collector, site, bot and tunnel | each affected service | timetable database and secrets |
| `python deploy/push.py --timetable PATH` | one already-built timetable | collector, site and bot | application code, tunnel, secrets |
| `python deploy/push.py --refresh-timetable` | builds and validates locally, then replaces the timetable | collector, site and bot | application code, tunnel, secrets |
| `python deploy/push.py --dry-run --all` | prints the scope | none | everything |

`--refresh-timetable --no-download` reuses the existing local GTFS input.
`--all` deliberately does not rebuild or replace the timetable.

## What a code deployment does

1. Runs that component's local tests and build, then the repository secret and
   public-metadata scans.
2. Creates a complete release with a SHA-256 manifest. `.env`, credentials and
   SQLite state are forbidden release inputs.
3. Uploads to a temporary name, verifies the archive and every manifested file,
   then installs dependencies in the new release while the old one stays live.
4. Atomically switches `~/bristolbusbot/current/<component>` on the Pi.
5. Restarts only the affected systemd service and runs its component-specific
   health check.
6. If health fails, atomically restores the previous link, restarts it and
   verifies recovery. Slack notifications are best-effort and never decide the
   deployment result.

A targeted deployment sends one success alert for that component. `--all`
sends one combined success alert after every component passes; failures still
identify the affected component immediately.

Production settings remain under `/etc/bristolbusbot`; mutable databases remain
under `/var/lib/bristolbusbot`. Current code releases are under
`~/bristolbusbot/releases` on the Pi.

Database initialisation is idempotent. Any incompatible schema change must use
an explicit migration with a documented rollback rather than running silently
during application startup.

## Timetable safety

The timetable path must be a regular SQLite file using DELETE journal mode. It
must pass integrity, service-date freshness, required First-route and route-shape
checks locally and again on the Pi. Promotion uses a fixed staging path and an
atomic rename while retaining `timetable.db.previous`. Collector, site and bot
must all recover; otherwise the previous database is restored automatically.

`pipeline/build_timetable.py` is invoked by `push.py`; production promotion
always goes through the deployment command.

## One-time layout installation

`python deploy/push.py --install-layout` is a one-time bootstrap, not a routine
deploy command. It creates the release/current directories, installs the exact
sudo allowlist and release-aware systemd units, initially links to the existing
known-good folders, and verifies every service and timer. It restores the old
unit files if any health gate fails.

## Other deployment tooling

Backup and credential-configuration tools remain separate
because they are destructive or interactive operational procedures, not code
deployments. See `docs/DEPLOYMENT.md` for the overall production shape. Real
secrets must never be
printed, copied into this repository or passed on a command line.

To rotate the bot control token after the unified layout has been installed:

```powershell
python deploy/rotate_bot_token.py --output "$HOME/.bbb-bot-api-token"
```

The output file must be outside the repository. The command uploads a private
candidate, validates it through the exact sudo-allowlisted helper, restarts
`bbb-bot.service`, and automatically restores the previous environment if the
systemd health gate fails. Neither token value is printed.
