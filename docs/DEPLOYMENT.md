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
read-only and can write only `/var/lib/bristolbusbot/social/` (the SQLite
ledger and rendered cards) and its monitoring job record. Its token is a
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

Commissioning note (4 August 2026): the channel, allowlisted user and root-only
bot token were configured successfully and the bot was confirmed as a private
channel member. The first shadow start exposed a local compatibility bug: the
credential reader rejected systemd's protected runtime credential copy because
its copied mode differed from the root-owned source file's `0600`. No Slack
message or card was sent. The follow-up fix accepts only the exact regular
`slack-token` beneath systemd's `/run/credentials/` hierarchy; arbitrary loose
or symlinked credentials remain rejected.

The rerun then exposed a separate sandbox-path error before Slack polling:
SQLite could open the top-level ledger but could not create its WAL sidecars
without write access to the whole shared state directory. No Slack message or
card was sent. The ledger therefore lives at
`/var/lib/bristolbusbot/social/social.db`, inside the service's dedicated
writable directory. The layout installer stops the social timer/service,
atomically migrates the database and any sidecars plus the non-secret config
path, validates the result, and restores the old locations on installer
rollback. It does not alter the root-only token.

Production status (4 August 2026): PR #32 was merged and social release
`20260803t234404745058z-4ab3bcea` plus the reviewed layout were installed. The
release passed its native ARM64 gate. The service and timer are inactive, the
timer is disabled, and no social credential or live marker exists. All four
core services and the local bot/site and public site health endpoints passed.
The failed-unit read-back also exposed an older, unrelated audit-rollup failure
from 3 August caused by the then-current pipeline release omitting
`fbribuses.json`. The repair is deliberately separate from the social rollout:
the private generated file lives at
`/var/lib/bristolbusbot/enrichment/fbribuses.json`, the root-owned rollup
wrapper fixes that path, systemd grants read-only access, and pipeline setup and
health gates fail closed if it cannot be parsed. Code releases do not contain
or overwrite the fleet file.

Production recovery completed on 4 August 2026. PR #34 merged as `f7e83307`;
pipeline release `20260804t001717496979z-f7e83307` passed the durable-file gate.
The validated live bot copy was atomically seeded with SHA-256
`69b953091c942005908546f2e30a74656100fc4666f5b32820a428868b7be976`
(2,605 vehicles, 4,386 lookup entries). The missing 2 August rollup and publish
then succeeded, aggregate health reported `ok` with no issues, all core
services and audit timers were active, and no failed units remained.

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

The example backup manifest includes the durable private enrichment directory
as an optional path so installations can adopt this repair before the wider
enrichment estate is fully migrated. The live root-owned backup configuration
must be checked separately before claiming backup coverage.

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
