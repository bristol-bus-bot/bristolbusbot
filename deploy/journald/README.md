# Persistent Pi logs

Raspberry Pi OS ships `40-rpi-volatile-storage.conf`, which explicitly sets
`Storage=volatile`. The existing `20-bristolbusbot.conf` only capped sizes;
creating `/var/log/journal` did not override the distribution's setting.

Install the reviewed template with `sudo sh deploy/install_persistent_journal.sh
deploy`. The later `90-` drop-in enables persistence with a 500 MiB cap and
1 GiB free-space reserve. This restarts only journald and flushes the current
runtime journal to disk. Application services remain running. No reboot is
needed. Previous volatile boot logs cannot be recovered.

Verify effective configuration with `systemd-analyze cat-config
systemd/journald.conf`, persistent journal files under `/var/log/journal`, and
`journalctl --list-boots` after the next natural reboot. Fourteen days is a
maximum retention age; the size cap can expire records earlier. Monitor actual
retention rather than promising seven days before observing the log volume.
