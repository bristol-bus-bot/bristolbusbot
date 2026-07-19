# BristolBusBot systemd units

These are source-controlled unit templates. Account and home-directory tokens
are rendered from the Git-ignored `deploy/local.env` by
`python deploy/push.py --install-layout`; do not copy the raw templates into
`/etc/systemd/system` or edit installed copies by hand.

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
