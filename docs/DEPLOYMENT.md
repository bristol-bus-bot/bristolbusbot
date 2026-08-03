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
named Cloudflare tunnel — plus twelve project timers owning the audit
rollup/publish/snapshot, collector staleness check, twice-daily digest,
nightly backup, weekly backup-repository check, resource sampling, aggregate
health, timetable delivery, approved editorial refresh and optional Slack card
curation. The curation timer is installed disabled until its shadow and
attended-delivery gates pass. Unit templates are source-controlled in
`deploy/systemd/` and rendered by `deploy/push.py --install-layout`; do not edit
live copies. Re-run that command after reviewed unit or deployment-helper
changes. It preserves the current release links and rolls the installed units
back if the live health gates fail.
Units use `Restart=always`, sandboxing (`ProtectSystem=strict`, exact
`ReadWritePaths`, `IPAddressDeny=any` for networkless jobs) and
`Persistent=true` timers with locking so delayed or coincident runs are
safe. Cross-privilege lock files are pre-created by `systemd-tmpfiles` as
deploy-user-owned coordination files; root jobs reuse those inodes rather than
creating private locks that later exclude an unprivileged job.

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
   affected long-running service; the social oneshot is not started by a code
   deployment;
5. accepts the release only after its component health gate passes —
   otherwise it restores the previous link automatically.

Deployment refuses a dirty working tree so every release maps to one
commit. SSH host keys are strictly checked and never auto-accepted.

## Timetable refreshes

GitHub Actions is the normal compute plane; the Pi is the scheduler and safety
plane. The daily `bbb-timetable-shadow.timer` causes a fresh build about every
six days, downloads only the exact default-branch artifact, and independently
checks its provenance, hash, schema, service horizon, routes, shapes and usable
dated service. Raw trip and stop-time totals remain diagnostics with only a low
catastrophic floor. The unprivileged downloader cannot write production data.

A separate fixed-path root service performs the atomic live replacement. It
retains `timetable.db.previous`, restarts and checks collector, site and bot,
checks the public endpoint, and restores the old database after any failure.
Automatic promotion requires a root-owned enable marker and never retries the
same rejected candidate automatically. Exact-run shadow delivery has no
automatic promotion edge; attended promotion must name the exact reviewed
run/hash, and automatic promotion refuses attended shadow state. Its detailed
result and timer job record feed one correlated aggregate health transaction
and the digest reads only that aggregate result.

This path is live, not shadow-only: GitHub run `29944744744` was downloaded,
validated and accepted by the production `auto` promotion path on 22 July
2026. The candidate carried service through 30 May 2027 and all consumer and
functional health gates passed. That commissioning run was manually initiated.

The first genuinely due timer run, `30421182234` on 29 July, built successfully
but was rejected before promotion. The existing flat total-row comparison
mistook the removal of superseded timetable editions for missing current
service; a direct 7/14/28-day comparison found the candidate at 100.3% of the
accepted database. Production was never changed and remains healthy. A
service-window validator and correlated monitoring correction are implemented
in repository source, but they must pass a promotion-disabled Pi shadow before
another live swap. Do not lower or bypass the installed count threshold as a
shortcut.

The later “promotion rejected / unknown_failure” Slack alert was a monitoring
false positive: a failed shadow correctly prevented promotion, but the monitor
independently aged the last promoter success and reused an older `no_change`
detail. The corrected source correlates wrapper/detail records by run and hash,
preserves last accepted state separately, and retries notification until Slack
confirms success.

`python deploy/push.py --refresh-timetable` remains the attended workstation
fallback. It applies the same validation, fixed staging, atomic replacement and
consumer rollback rules.

## Approved editorial information

The bot's sourced facts, transport occasions and short-lived news are stored in
`bot/data/editorial-context.json`. GitHub's `editorial-news.yml` checks official
Department for Transport results and opens a normal pull request for a relevant
new story. The PR is the approval screen: merge approves the claim and its
machine-checkable `requirements`; edit then merge approves the edited version;
close rejects it. The discovery job supplies a conservative first checklist of
the title, figures and dates. Reviewers should add or improve natural
alternatives before merging when a material scope or qualification is not
captured automatically. Source URLs remain private provenance and are never
appended to public posts.

Gemini 3.6 Flash writes one finished post. Ordinary posts take one model call.
For an editorial post, code first requires the route, direction, stop, observed
timing and every approved requirement group, then a separate non-writing
verifier may only pass or fail the facts. A failed or unsuitable hook is not
counted as used: the bot publishes an ordinary observation and defers that hook
for six hours (two hours for a date-specific occasion). The emergency
`AI_COMMENTARY_PIPELINE=legacy` environment setting restores the previous
writer/critic path without a code rollback; `single` is the default.

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

## Slack card curation

`python deploy/push.py --component social` builds the isolated social release,
runs its Python and JavaScript tests, installs Node dependencies off to the
side, then accepts it only after a native ARM64 1080 x 1350 render. It does not
contact Slack, start the curation service or change the timer. `--all`
deliberately excludes this optional component.

`--install-layout` installs `bbb-social-curation.service` and its timer but
leaves that timer disabled. The oneshot reads the bot and audit databases
read-only and can write only `/var/lib/bristolbusbot/social.db`,
`/var/lib/bristolbusbot/social/` and its monitoring job record. Its token is a
root-owned mode-0600 file loaded by systemd credentials, never an environment
variable, release file or command-line value.

Configure the private channel, allowlisted maintainer and hidden bot token once
on the Pi:

```sh
sudo /usr/local/sbin/bbb-configure-social-curation \
  --channel-id C_PRIVATE --allowed-user-id U_MAINTAINER
```

Without `/etc/bristolbusbot/social-live-enabled`, every run is shadow-only. The
first run seeds a current-time Slack checkpoint and cannot replay retained
history. After a newly shared link renders cleanly in shadow, one attended live
delivery uses the tightly allowlisted `social-live-enable` helper and a newly
shared Slack message. A shadowed message has already been checkpointed and is
never silently replayed. Enable the timer only after checking the Slack image,
alt text, caption and SQLite ledger.
The kill switch is disabling the timer and removing the live marker; neither
operation touches Instagram or any core service.

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
- A first social release that fails its ARM64 render gate removes its new
  `current/social` link; it never starts a service or contacts Slack.
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
