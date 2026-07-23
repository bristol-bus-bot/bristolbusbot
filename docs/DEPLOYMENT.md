# Deployment

How production is shaped and operated, in generic terms. Placeholders
`<deploy-user>` and `<pi-host>` avoid publishing production identity. Local
setup is documented in the component READMEs.

## Layout on the Pi

| Path | Contents |
|---|---|
| `/home/<deploy-user>/bristolbusbot/releases/<component>/<release>` | immutable, hash-manifested code releases |
| `/home/<deploy-user>/bristolbusbot/current/<component>` | atomic symlink selecting the live release |
| `/etc/bristolbusbot` | root-owned configuration and secrets (mode 0600) |
| `/var/lib/bristolbusbot` | durable mutable state: the SQLite databases, monitoring job records, the audit integration snapshot |

Service code is read-only at runtime. A release can never contain
`.env` files, credentials or databases; those belong to the host.

The production SSH username, hostname, remote home, backup filesystem UUID,
Cloudflare tunnel UUID and workstation GTFS path live only in the Git-ignored
`deploy/local.env`. The committed `deploy/local.env.example` contains Darkplace
fictional values. Deployment renders systemd and tunnel templates into a
temporary payload, so the public repository does not reveal the maintainer's
machine identity.

## Services and timers

Four long-running system-level units — collector, site, bot and the
named Cloudflare tunnel — plus ten project timers owning the audit
rollup/publish/snapshot, collector staleness check, twice-daily digest,
nightly backup, weekly backup-repository check, resource sampling, aggregate
health and timetable delivery. Unit templates are source-controlled in
`deploy/systemd/` and rendered by `deploy/push.py --install-layout`; do not edit
live copies. Re-run that command after reviewed unit or deployment-helper
changes. It preserves the current release links and rolls the installed units
back if the live health gates fail.
Units use `Restart=always`, sandboxing (`ProtectSystem=strict`, exact
`ReadWritePaths`, `IPAddressDeny=any` for networkless jobs) and
`Persistent=true` timers with locking so delayed or coincident runs are
safe.

The site binds to loopback and is published only through the named
tunnel; the bot API binds to loopback and its control endpoints require
a bearer token.

## Deploying code

`python deploy/push.py` is the only supported deployment interface; the
exact scope of every command is tabulated in `deploy/README.md`. A code
deployment:

1. runs the component's local tests/build and the repository secret scan;
2. packages a release with a SHA-256 manifest;
3. uploads, verifies and installs dependencies off to the side while the
   old release stays live;
4. atomically switches the `current` symlink and restarts only the
   affected service;
5. accepts the release only after its component health gate passes —
   otherwise it restores the previous link automatically.

Deployment refuses a dirty working tree so every release maps to one
commit. SSH host keys are strictly checked and never auto-accepted.

## Timetable refreshes

GitHub Actions is the normal compute plane; the Pi is the scheduler and safety
plane. The daily `bbb-timetable-shadow.timer` causes a fresh build about every
six days, downloads only the exact default-branch artifact, and independently
checks its provenance, hash, schema, service horizon, routes, shapes and row
count changes. The unprivileged downloader cannot write production data.

A separate fixed-path root service performs the atomic live replacement. It
retains `timetable.db.previous`, restarts and checks collector, site and bot,
checks the public endpoint, and restores the old database after any failure.
Automatic promotion requires a root-owned enable marker and never retries the
same rejected candidate automatically. Its detailed result and timer job record
feed aggregate health.

This path is live, not shadow-only: GitHub run `29944744744` was downloaded,
validated and accepted by the production `auto` promotion path on 22 July
2026. The candidate carried service through 30 May 2027 and all consumer and
functional health gates passed. That commissioning run was manually initiated;
the 05:00 timer is enabled but had not yet fired as of that date. Its first due
rebuild remains routine monitoring rather than a remaining implementation
gate; the workstation is retained only as an attended fallback.

`python deploy/push.py --refresh-timetable` remains the attended workstation
fallback. It applies the same validation, fixed staging, atomic replacement and
consumer rollback rules.

## Approved editorial information

The bot's sourced facts, transport occasions and short-lived news are stored in
`bot/data/editorial-context.json`. GitHub's `editorial-news.yml` checks official
Department for Transport results and opens a normal pull request for a relevant
new story. The PR is the approval screen: merge approves the exact wording;
edit then merge approves the edited wording; close rejects it.

On the Pi, `bbb-editorial-refresh.timer` checks the file on `main` every 30
minutes. The unprivileged fetcher accepts only the fixed repository, branch and
path, then applies byte, schema, date, source-host, duplicate-ID and content
limits. A separate root promoter validates the same bytes again, keeps one
`.previous` copy, replaces the live file atomically and restarts the bot. It
accepts the change only when `/api/health` reports the exact promoted SHA-256;
otherwise it restores the previous file and restart state. Aggregate health
sends one detailed Slack success or failure transition.

For the first deployment of this feature, deploy the bot release before the
layout so the restarted service understands the new health field:

```powershell
python deploy/push.py --component bot
python deploy/push.py --install-layout
```

Then verify on the Pi:

```sh
sudo systemctl start bbb-editorial-fetch.service
systemctl status bbb-editorial-fetch.service bbb-editorial-promote.service
systemctl status bbb-editorial-refresh.timer
curl -fsS http://127.0.0.1:3010/api/health
```

No GitHub token is stored on the Pi for this path because the approved source
file is public. A validation, download, restart or digest failure leaves the
previous approved information live.

## Backups

Nightly encrypted restic snapshots to a dedicated local drive, copied to
off-site object storage as a separate observable stage. Live SQLite
databases are snapshotted via the SQLite backup API and integrity-checked
before capture — never raw-copied while being written. Retention is 7
daily / 4 weekly / 6 monthly in both repositories. A weekly job reads
back the local repository in full and rotates through the off-site packs.
An external dead-man service alerts on missed runs, so a silently dead
host is noticed. Restores are drilled from both repositories to scratch
directories with manifest, integrity and freshness verification; a backup
that has not been restored is not treated as a backup.

## Rollback rules

- A component deploy rolls itself back when its health gate fails.
- The timetable deploy retains the previous database on the Pi.
- If the tunnel is unhealthy, inspect its logs and named-tunnel
  configuration; do not replace it with an ad-hoc quick tunnel.

## Inspecting production

```bash
ssh <deploy-user>@<pi-host> "systemctl status bbb-collector bbb-site bbb-bot bbb-tunnel --no-pager"
ssh <deploy-user>@<pi-host> "sudo journalctl -u bbb-collector -n 30 --no-pager"
ssh <deploy-user>@<pi-host> "curl -fsS http://127.0.0.1:5002/healthz"
ssh <deploy-user>@<pi-host> "curl -fsS http://127.0.0.1:3010/api/health"
ssh <deploy-user>@<pi-host> "systemctl list-timers --all --no-pager | grep bbb-"
```

Never place `.env` contents, tokens or app passwords in logs, issue trackers
or version control.
