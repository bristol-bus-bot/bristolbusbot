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
| `python deploy/push.py --component bot` | locally built bot and Node dependencies | bot | collector, site, tunnel, durable enrichment, state, secrets |
| `python deploy/push.py --component pipeline` | scheduled audit job code and reviewed audit-site assets | none | timetable, live services, secrets |
| `python deploy/push.py --component tunnel` | non-secret named-tunnel ingress config | tunnel | credential JSON and application code |
| `python deploy/push.py --component social` | isolated renderer and Slack-curation code | none; ARM64 render health gate only | core services, timer state, credentials, databases |
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

Every one-shot SSH command and upload is wall-clock bounded as well as using
OpenSSH's connection and encrypted-channel keepalives. The deployer opens one
private, silent SSH session anchor before the safety check and holds it until
that deployment ends.
This prevents the Raspberry Pi's `Linger=no` user session being torn down
between the safety check and `scp`, and does not require a separate keeper
terminal. Closing the deployer closes the anchor's private stdin, so it also
exits after a normal finish or if the parent process disappears. The initial
safety check gets 20 seconds and one automatic retry while that anchor remains
open. Ordinary remote commands get 15 minutes and uploads get 30 minutes.
Captured output is written to temporary files rather than process pipes, so a
descendant that inherits an output handle cannot make the Windows deploy
command wait forever. On timeout the deployer stops only the private process
tree it created and does not continue to the next deployment step. Strict
host-key checking, batch-mode authentication, atomic switching, health gates
and rollback remain mandatory.

### If the SSH safety check times out

The deployer stops the first timed-out check and retries once. If both bounded
attempts time out, its final error explicitly confirms that nothing was
uploaded and no release was switched. At that point:

1. Leave the deploy command stopped; do not bypass its SSH options and do not
   open a keeper session.
2. Check that the configured Pi hostname is reachable on the local network and
   that a normal `ssh USER@HOST true` returns. Interrupt that diagnostic if it
   stalls; it is not a deployment.
3. Correct the connectivity or Pi SSH/user-session problem, then rerun the
   original deployment command unchanged. Its clean-tree and atomic-release
   checks make that retry safe.

A later remote-command timeout may occur after files have been staged or an
atomic switch has been attempted, so read the reported command and verify the
current release before retrying. The timeout still stops further steps; it
never turns off rollback or broadens the sudo allowlist.

The optional social component is intentionally more conservative. Its release
gate performs one native ARM64 demo render on the Pi, then removes that demo;
it never starts the curation service or calls Slack. `--install-layout`
installs its oneshot and timer but leaves the timer disabled. Configure the
private Slack channel, allowlisted user and hidden bot token with
`bbb-configure-social-curation`, prove one shared link in shadow mode, then use
the allowlisted live marker for one attended delivery before enabling the
timer. The full commands and kill switch are in `social/README.md`.

Production settings remain under `/etc/bristolbusbot`; mutable databases remain
under `/var/lib/bristolbusbot`. Current code releases are under
`~/bristolbusbot/releases` on the Pi.

The CARTO browser key is installed separately from code releases. Run
`python deploy/configure_carto_key.py`, paste the complete CARTO tile URL from
the project's key email into the hidden prompt, and let the guarded helper
validate, restart and health-check the site. The value is not written to the
workstation, passed in a command argument or printed. A site code deployment
requires the key to be present in `/etc/bristolbusbot/site.env` and refuses to
proceed when it is missing.

### Private collector research dataset

Run this from the repository root when an expert needs a broad, machine-readable
copy of the collector history:

```powershell
python deploy/get_collector_research_export.py
```

The command asks the Pi to build a full census of every retained closed service
day, downloads it to `Downloads`, verifies its size, hashes, ZIP contents,
SQLite integrity and every table row count, then removes the temporary Pi copy.
The resulting ZIP contains `collector-research.sqlite` plus a plain-English
`README.txt`, data dictionary, regime-change timeline and prominent caveats.
It deliberately omits coordinates, raw nested JSON and any future database
column that has not been explicitly reviewed for export. The production audit
database is opened read-only and is first copied using SQLite's online backup
API under the shared heavy-I/O lock.

This is private research material, not a public download. The helper refuses
repository and known public-site paths, never replaces an existing file, and
normally leaves no archive on the Pi. A broken connection prints an exact
`--resume` command; if only remote cleanup failed, it prints an exact
`--cleanup` command. To request a shorter closed period or a different private
destination, use `--from YYYYMMDD`, `--to YYYYMMDD` and `--output PATH`.

Bot enrichment is also durable state. `--install-layout` safely seeds any
missing `fbribuses.json`, `stop_localities.json`, `stop_enrichment.json`,
`local_flavour.json` and `route_details.json` files from the currently verified
bot release into `/var/lib/bristolbusbot/enrichment`, preserves any files
already authoritative there, validates all five, and makes that directory a
required backup source. Run this layout migration before deploying the first
bot release that omits those files. The bot health gate then requires the
durable layout, both databases and a non-empty fleet before accepting the new
release.

`--install-layout` also installs a networkless, read-only data-health job at
04:15 each day. It compares recently observed operator-scoped vehicles with the
fleet, livery and three description inputs, checks timetable stop localities,
and detects a collapsed per-operator fleet count. It writes only
`/var/lib/bristolbusbot/monitoring/data-health.json`; findings are report-only
and appear in the twice-daily digest. A failed or stale job is an operational
health problem, but a completeness warning cannot edit or promote any data.

It also installs `bbb-evidence-pack`, an attended private diagnostic for an odd
reading. Select a service date, exact bus reference, timetable/SIRI trip
reference or saved evidence ID; selectors can be combined to narrow the result:

```sh
bbb-evidence-pack --date 2026-08-23 --bus FICT-0001 \
  --output "$HOME/odd-reading-2026-08-23.json"
```

The command opens the production audit and timetable databases read-only. It
reports the full number of matching receipts but includes at most 25 spread
across the selected time range, limits observations, polls and timetable stops,
and reduces that representative sample further if needed to stay below the hard
512 KiB final limit. The output is an atomic mode-0600
JSON file and an existing file is not replaced unless `--force` is supplied.
Known public website and audit-repository paths are refused. Cause labels are
triage clues rather than proof, and missing historical observations or
timetable editions remain explicitly unavailable. Run `bbb-evidence-pack
--help` for all selectors; the command cannot change either database or any
published statistic.

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
the heavy build; `bbb-timetable-shadow-auto.service` handles scheduled checks,
while `bbb-timetable-shadow@RUN_ID.service` downloads one exact successful
default-branch run without triggering promotion. Both safely extract the
three-file parcel, verify its GitHub digest and provenance manifest, validate
the database again, and compare usable dated service with the current database.
Their systemd sandboxes can write only under
`/var/lib/bristolbusbot/timetable-shadow`, monitoring state and its lock file.
They have no restart permission, promotion command or writable production path.

After the automatic shadow unit succeeds, systemd starts the separate root
`bbb-timetable-promote@auto.service`. Its identity-first fast path skips an
already handled run without reopening the candidate database, and it refuses a
shadow recorded as attended. Automatic mode remains
fail-closed until `/etc/bristolbusbot/timetable-promotion-enabled` exists as a
root-owned regular file with mode `0644`. The promoter accepts no paths: it
re-verifies the fixed candidate, copies it to fixed production staging, checks
the hash again, atomically promotes it, restarts the three consumers, and checks
local health, the timetable-backed stop-search endpoint, the bot's nested
timetable connection and public health.
Failure after replacement restores
`timetable.db.previous`; an automatically rejected candidate is not retried
until a different candidate arrives.

Aggregate health treats delivery and promotion as one run/hash-correlated
transaction and sends one detailed Slack message when a different timetable is
accepted. It includes coverage, row counts, source/fallback status, database and
GitHub-run identity, functional-check results and rollback readiness. A failed
delivery or promotion alert names the failure and explicitly says whether the
candidate never reached production, the previous database was restored, or
automatic rollback could not prove recovery. Alerts are recorded as delivered
only after Slack returns success. Daily no-change checks stay quiet, and the
twice-daily digest reads the same aggregate transaction state.

Production status (29 July 2026): the timer and automatic-promotion marker are
enabled. GitHub run `29944744744` completed the full production `auto` path and
was accepted after database, consumer, stop-search and public-health gates. The
first timer-triggered due build, run `30421182234`, was safely rejected before
promotion by the total `stop_times` collapse gate even though its next 28 days
of service were complete. The live database was not changed. The correction is
implemented in repository source but is not live until it passes the attended
shadow and exact-hash promotion gates. Do not use a broad force or lower the old
floor on the installed service.

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

Routine timer runs use the dedicated automatic unit. An exact-run instance is
diagnostic only and cannot chain into the promoter. Stop the timer and disable
the root promotion marker during rollout, then run the reviewed numeric GitHub
run ID:

```sh
sudo systemctl start bbb-timetable-shadow@RUN_ID.service
sudo journalctl -u bbb-timetable-shadow@RUN_ID.service --since today
```

Read the resulting run ID and full database SHA-256 from
`/var/lib/bristolbusbot/timetable-shadow/state.json`, compare them with the
reviewed artifact, and explicitly promote only that identity:

```sh
sudo systemctl start bbb-timetable-promote@RUN_ID-SHA256.service
sudo journalctl -u bbb-timetable-promote@RUN_ID-SHA256.service -n 100 --no-pager
```

The promoter refuses a run/hash that differs from the latest successful shadow.
Restore the marker and timer only after the attended transaction and all health
evidence are complete.

The daily timer checks the live database and monitoring state every morning. A
successful automatic shadow delivery starts a six-day cooldown, producing
roughly one fresh GitHub build per week; a failed due run retries the next day.
The 28-day service-coverage signal remains a safety warning and validator input,
but a far-future service date never postpones the normal weekly refresh.

### Approved editorial delivery

`bbb-editorial-refresh.timer` checks every 30 minutes for the human-approved
`bot/data/editorial-context.json` on `main`. The unprivileged fetch unit writes
only to `/var/lib/bristolbusbot-editorial/incoming`; the separate root promoter
revalidates the fixed candidate, keeps one previous copy, replaces atomically,
restarts only the bot and verifies the exact SHA-256 through `/api/health`.
Failures retain or restore the previous approved file.

Deploy the compatible bot before installing this layout for the first time:

```powershell
python deploy/push.py --component bot
python deploy/push.py --install-layout
```

The installer validates and seeds the durable file, then enables the timer.
No credential is needed because only a public file from the fixed repository,
branch and path is accepted. Aggregate health records both jobs and sends a
single detailed Slack message when a new blob is accepted or a transition
fails.

If the live hash, consumer services and public health are correct after the
run/hash-pinned attended transaction above, enable the fixed marker and exercise
automatic no-change handling:

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
deployments. This README is the public production contract. Real secrets must never be
printed, copied into this repository or passed on a command line.

To rotate the bot control token after the unified layout has been installed:

```powershell
python deploy/rotate_bot_token.py --output "$HOME/.bbb-bot-api-token"
```

The output file must be outside the repository. The command uploads a private
candidate, validates it through the exact sudo-allowlisted helper, restarts
`bbb-bot.service`, and automatically restores the previous environment if the
systemd health gate fails. Neither token value is printed.
