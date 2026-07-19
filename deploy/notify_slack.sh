#!/usr/bin/env bash
# Send a deployment or monitoring message to Slack.
# Reads the incoming-webhook URL from a gitignored config file:
#   $SLACK_WEBHOOK_FILE, or ~/.config/busbot-alerts/webhook
# Usage:  notify_slack.sh "message text"
# Never fails its caller: a missing webhook or a failed post is logged and ignored.

set -uo pipefail

CONF="${SLACK_WEBHOOK_FILE:-$HOME/.config/busbot-alerts/webhook}"

if [ ! -f "$CONF" ]; then
    echo "notify_slack: no webhook config at $CONF, skipping" >&2
    exit 0
fi

URL="$(head -n1 "$CONF" | tr -d '[:space:]')"
if [ -z "$URL" ]; then
    echo "notify_slack: webhook config is empty, skipping" >&2
    exit 0
fi

TEXT="${1:-}"
PAYLOAD="$(printf '%s' "$TEXT" | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')"

curl -s -m 10 -X POST -H 'Content-type: application/json' --data "$PAYLOAD" "$URL" >/dev/null \
    || echo "notify_slack: post failed" >&2

exit 0
