# BristolBusBot systemd units

These are source-controlled unit templates. Account and home-directory tokens
are rendered from the Git-ignored `deploy/local.env` by
`python deploy/push.py --install-layout`; do not copy the raw templates into
`/etc/systemd/system` or edit installed copies by hand. Re-run the command after
a reviewed unit or deployment-helper change; it preserves the selected release
links and restores the prior units if a health gate fails.

The units execute through `~/bristolbusbot/current/<component>` on the
Pi. Install or update releases only with `python deploy/push.py`; do not
edit installed units, current symlinks or release directories by hand.
The one-time layout installer runs `systemd-analyze verify`, backs up
installed units and restores them if any service or public health check
fails.

Each service's environment file lives at `/etc/bristolbusbot/<name>.env`,
owned root-readable by the service user with mode `0640`.

The social Slack bot token is the deliberate exception: it lives alone at
`/etc/bristolbusbot/social-slack.token`, root-owned with mode `0600`, and
systemd presents it only to `bbb-social-curation.service` through
`LoadCredential`. It must never be placed in an environment file, command
line, release, log or chat message.

The units enable resource accounting immediately.
`bbb-resource-sample.timer` records RSS every five minutes. Memory
limits are intentionally not guessed: collect at least seven days, run
`bbb-sample-resources --report`, then set `MemoryHigh` near 1.5× p95 and
`MemoryMax` near 2× p95 with practical headroom.

The timer units own all scheduled jobs; there are no project cron commands.
The unified layout installer verifies every unit before replacing installed
copies.

`bbb-social-curation.timer` is also excluded from automatic enablement. The
layout installer leaves it disabled, and the service defaults to shadow mode
until `/etc/bristolbusbot/social-live-enabled` exists. Its first successful
poll records the current Slack timestamp without reading retained history, so
installing it cannot replay old channel links. Enable live mode for one
attended delivery only after shadow verification; enable the timer only after
that delivered card, alt text, caption and ledger entry have been checked.
Disabling the timer and running `bbb-deploy-control social-live-disable` is the
kill switch and does not affect any core service.

`bbb-timetable-shadow.timer` is the one credential-gated exception to automatic
enablement: the layout installer leaves it disabled until the root-only GitHub
credential exists. The timer starts `bbb-timetable-shadow-auto.service`; only
that automatic unit chains to the separately sandboxed root
`bbb-timetable-promote@auto.service`, which remains structurally disabled
without the root-owned promotion marker. An operator can start
`bbb-timetable-shadow@RUN_ID.service` for one diagnostic exact-run delivery; it
has no promotion chain. After reviewing the shadow state, the live swap must use
`bbb-timetable-promote@RUN_ID-SHA256.service` with the exact recorded identity.
The automatic promoter also rejects a latest shadow whose recorded mode is
attended, closing the gap between the two units.

Production status (29 July 2026): the credential, timer and root promotion
marker are installed; a complete production `auto` delivery and promotion was
accepted successfully after being manually initiated during commissioning.
The first timer-triggered due build was safely rejected before promotion by an
over-broad raw-row gate. Remediation is documented in the timetable execution
plan.

The corrected source templates keep exact-run and automatic topology separate.
They use exit 73 for `flock` timeout, which the job wrapper records as
`lock_timeout`; exit 75 is reserved for a benign application skip. These source
templates are not production evidence until the reviewed layout is installed
and the promotion-disabled shadow/attended rollout passes.

`bbb-enrichment-promote@.service` is an attended-only, dormant data promoter.
It has no timer and its Python entrypoint accepts only the fixed names `fleet`
and `localities`; it does not accept source or destination paths. Each run uses
the same heavy-I/O lock as backup and timetable work, revalidates the candidate,
keeps one previous file, restarts the site and bot, and requires their reported
digest and record count before accepting the swap. Installing the unit does not
stage or promote any data. A real promotion remains a separate reviewed action.

The human-curated `model-context.json` is different from downloaded enrichment
data: an attended unified-layout install synchronises its reviewed repository
copy atomically. The sync rejects removals, large batches of additions or broad
rewrites, and the installer's normal file backup and rollback cover the change.
It never edits or promotes any public description file.

Pending description generation retains the terse, sardonic voice already used
by the approved public description files, with separate in-service, waiting and
depot examples. Selection round-robins operator/model groups and caps a single
review batch at 20 identities from one operator and 10 from one model. Strict
validation rejects generic brochure language, US spellings and claims of an
unsupplied live battery state. Generation still writes only a pending batch;
review, approval and promotion remain separate attended actions.

Stop localities use a stricter three-step chain. After a successful timetable
promotion check, `bbb-locality-refresh.service` builds a shadow candidate from
the exact live timetable and the fixed ONS December 2025 ward endpoint.
`bbb-locality-stage.service` independently rechecks freshness, hashes and exact
stop-code coverage before `bbb-enrichment-promote@localities.service` performs
the atomic swap. The site and bot must then report the exact promoted digest
and record count or the previous locality file is restored. The separate
`bbb-locality-shadow.service` has no promotion edge and always refetches the
approved boundary edition for attended inspection. Automatic triggering is
installed off until the root-owned `locality-refresh-enabled` marker is created
through the fixed deploy control; removing that marker pauses future locality
refreshes without disabling timetable updates or changing the live file.
