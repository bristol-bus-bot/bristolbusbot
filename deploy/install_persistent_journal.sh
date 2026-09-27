#!/bin/sh
# Narrow installer: retain logs without restarting application services.
set -eu
stage=${1:?usage: install_persistent_journal.sh DEPLOY_DIRECTORY}
test "$(id -u)" -eq 0
source_file="$stage/journald/90-bristolbusbot-persistent.conf"
test -f "$source_file"
install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
install -o root -g root -m 0644 "$source_file" /etc/systemd/journald.conf.d/90-bristolbusbot-persistent.conf
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald.service
journalctl --flush
journalctl --sync
systemctl is-active systemd-journald.service
journalctl --disk-usage
