#!/bin/sh
set -eu
exec /usr/bin/python3 \
    /usr/local/libexec/bristolbusbot-enrichment/blurb_automation.py "$@"
