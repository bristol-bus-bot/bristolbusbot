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

### Automated GitHub timetable delivery

Download and promotion remain separate privilege boundaries. GitHub performs
the heavy build; `bbb-timetable-shadow@.service` downloads one exact successful
default-branch run, safely extracts the three-file parcel, verifies its GitHub
digest and provenance manifest, validates the database again, and compares its
counts with the current database. Its systemd sandbox can write only under
`/var/lib/bristolbusbot/timetable-shadow`, monitoring state and its lock file.
It has no restart permission, promotion command or writable production path.

After a successful or harmless skipped shadow check, systemd starts the
separate root `bbb-timetable-promote@auto.service`. Automatic mode remains
fail-closed until `/etc/bristolbusbot/timetable-promotion-enabled` exists as a
root-owned regular file with mode `0644`. The promoter accepts no paths: it
re-verifies the fixed candidate, copies it to fixed production staging, checks
the hash again, atomically promotes it, restarts the three consumers, and checks
local health, the timetable-backed stop-search endpoint and public health.
Failure after replacement restores
`timetable.db.previous`; an automatically rejected candidate is not retried
until a different candidate arrives.

Aggregate health sends one detailed Slack message when a different timetable is
accepted. It includes coverage, row counts, source/fallback status, database and
GitHub-run identity, functional-check results and rollback readiness. A failed
delivery or promotion alert names the failure and explicitly says whether the
candidate never reached production, the previous database was restored, or
automatic rollback could not prove recovery. Daily no-change checks stay quiet.

Production status (22 July 2026): the timer and automatic-promotion marker are
enabled. GitHub run `29944744744` completed the full unattended path and was
accepted after database, consumer, stop-search and public-health gates. The
steps below remain the installation/recovery procedure, not unfinished rollout
work.

`--install-layout` installs this service but leaves its daily timer disabled
until its root-only credential files exist. On the Pi, configure them without
putting the token in shell history:

```sh
sudo /usr/local/sbin/bbb-configure-timetable-delivery
sudo systemctl enable --now bbb-timetable-shadow.timer
```

Use a fine-grained token restricted to `bristol-bus-bot/bristolbusbot`, with
Actions read/write and no source-code write permission. The helper writes it
to `/etc/bristolbusbot/timetable-delivery.token` with mode `0600`. systemd
mounts that token privately into only the short-lived shadow service; it is not
placed in the service environment. Monitoring records only its expiry date.

Routine timer runs use the `auto` instance. For one attended shadow test of an
already successful workflow run, use its numeric GitHub run ID:

```sh
sudo systemctl start bbb-timetable-shadow@RUN_ID.service
sudo journalctl -u bbb-timetable-shadow@RUN_ID.service --since today
```

The daily timer checks the live database and monitoring state every morning. A
successful automatic shadow delivery starts a six-day cooldown, producing
roughly one fresh GitHub build per week; a failed due run retries the next day.
The 28-day service-coverage signal remains a safety warning and validator input,
but a far-future service date never postpones the normal weekly refresh.

Before enabling automatic promotion, run one attended transaction and inspect
the result:

```sh
sudo systemctl start bbb-timetable-promote@attended.service
sudo journalctl -u bbb-timetable-promote@attended.service -n 100 --no-pager
```

If the live hash, consumer services and public health are correct, enable the
fixed marker and exercise automatic no-change handling:

```sh
sudo /usr/local/sbin/bbb-deploy-control timetable-auto-enable
sudo systemctl start bbb-timetable-promote@auto.service
```

Emergency stop is immediate and does not disturb the current live timetable:

```sh
sudo /usr/local/sbin/bbb-deploy-control timetable-auto-disable
sudo systemctl disable --now bbb-timetable-shadow.timer
```

## Layout installation and updates

`python deploy/push.py --install-layout` creates the release/current directories,
installs the exact sudo allowlist, deployment helpers and release-aware systemd
units, and verifies every enabled service and timer. Existing `current` release
links are preserved. A newly installed credential-dependent timer may remain
disabled as documented above. Re-run it only when a reviewed helper or unit
template changes; it backs up and restores the installed units if any health
gate fails.

When a unit starts calling a renamed release file, deploy a release containing
both the old compatibility entry point and the new file before updating the
layout. This keeps both the old and new unit valid throughout the transition.

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
