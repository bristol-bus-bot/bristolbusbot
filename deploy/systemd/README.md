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

The units enable resource accounting immediately.
`bbb-resource-sample.timer` records RSS every five minutes. Memory
limits are intentionally not guessed: collect at least seven days, run
`bbb-sample-resources --report`, then set `MemoryHigh` near 1.5× p95 and
`MemoryMax` near 2× p95 with practical headroom.

The timer units own all scheduled jobs; there are no project cron commands.
The unified layout installer verifies every unit before replacing installed
copies.

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
