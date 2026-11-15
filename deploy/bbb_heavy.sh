#!/bin/sh
# bbb-heavy: run disk-heavy manual work on the Pi without starving the
# collector (planning #139).
#
#   bbb-heavy [--wait SECONDS] -- COMMAND [ARGS...]
#
# Holds the shared heavy-I/O lock, so it queues behind the backup, rollup and
# timetable jobs and they queue behind it, and runs the command at idle disk
# priority and the lowest CPU priority. The disks use the BFQ scheduler, which
# honours the idle class; the collector has the highest disk weight.
#
# Copies off the Pi go through it too, rate-limited, for example from the PC:
#   rsync --bwlimit=10000 --rsync-path="bbb-heavy -- rsync" pi:/path/file .
set -eu
LOCK=/run/lock/bristolbusbot/heavy-io.lock
WAIT=3600
if [ "${1:-}" = "--wait" ]; then
    WAIT=${2:?--wait needs a number of seconds}
    shift 2
fi
if [ "${1:-}" != "--" ] || [ "$#" -lt 2 ]; then
    echo "usage: bbb-heavy [--wait SECONDS] -- COMMAND [ARGS...]" >&2
    exit 64
fi
shift
echo "bbb-heavy: waiting up to ${WAIT}s for the heavy-I/O lock" >&2
exec /usr/bin/flock -w "$WAIT" -E 75 "$LOCK" \
    /usr/bin/ionice -c 3 /usr/bin/nice -n 19 "$@"
