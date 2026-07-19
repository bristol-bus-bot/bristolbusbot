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
named Cloudflare tunnel — plus nine timers owning the audit
rollup/publish/snapshot, collector staleness check, twice-daily digest,
nightly backup, weekly backup-repository check, resource sampling and an
aggregate health snapshot. Unit templates are source-controlled in
`deploy/systemd/` and rendered by `deploy/push.py --install-layout`; do not edit
live copies.
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

`python deploy/push.py --refresh-timetable` builds the three-layer
timetable on the workstation, validates it (integrity, service-date
freshness, expected routes, route shapes — all fail-closed), uploads to
a fixed staging name, promotes atomically while retaining the previous
database, then restarts and health-checks the collector, site and bot.
Any failed consumer restores the previous timetable.

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
