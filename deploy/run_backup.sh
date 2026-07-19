#!/bin/sh
set -eu
if [ "$(date +%u)" = 7 ]; then
    exec /usr/local/sbin/bbb-backup backup --integrity-check
fi
exec /usr/local/sbin/bbb-backup backup
