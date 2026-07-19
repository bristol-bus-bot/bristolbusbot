#!/usr/bin/env bash
# Runs on the Pi after the separate networkless rollup. Exports the static
# JSON, copies it into the public repo clone and pushes. Outbound only.
# Posts a Slack heartbeat on success and an alert on failure.
# Usage:  ./publish_to_github.sh

set -euo pipefail

REMOTE_HOME="${BBB_REMOTE_HOME:-$HOME}"
AUDIT_DIR="${BBB_PIPELINE_DIR:-$REMOTE_HOME/bristolbusbot/current/pipeline}"
AUDIT_SITE_DIR="${BBB_AUDIT_SITE_DIR:-/var/lib/bristolbusbot/pipeline/audit_site}"
PENDING_INTEGRATION="${BBB_AUDIT_INTEGRATION_PENDING:-/var/lib/bristolbusbot/collector/audit_integration.pending.json}"
PUBLISHED_INTEGRATION="$AUDIT_SITE_DIR/audit_integration.json"
REPO_DIR="${BBB_AUDIT_REPO_DIR:-$REMOTE_HOME/bus-audit-repo}"
DEPLOY_KEY="${BBB_AUDIT_DEPLOY_KEY:-$REMOTE_HOME/.ssh/bus_audit_deploy}"
ASSET_DIR="$AUDIT_DIR/audit_site_assets"
PY="$AUDIT_DIR/venv/bin/python3"
NOTIFY="${BBB_NOTIFY_SCRIPT:-$REMOTE_HOME/bin/notify_slack.sh}"
PROJECT="Bristol Bus Audit"

notify() { [ -x "$NOTIFY" ] && "$NOTIFY" "$1" || true; }
trap 'notify ":rotating_light: $PROJECT: nightly publish FAILED at line $LINENO. Check journalctl -u bbb-audit-publish.service"' ERR

export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes"

cd "$AUDIT_DIR"

BBB_AUDIT_SITE_DIR="$AUDIT_SITE_DIR" "$PY" audit_export.py

cd "$REPO_DIR"
git pull --ff-only origin main

install -m 0644 "$AUDIT_SITE_DIR/audit_data.json" docs/audit_data.json
install -m 0644 "$ASSET_DIR/index.html" docs/index.html
install -m 0644 "$ASSET_DIR/app.js" docs/app.js
install -m 0644 "$ASSET_DIR/styles.css" docs/styles.css
install -d -m 0755 docs/fonts
install -m 0644 "$ASSET_DIR/fonts/overpass-latin.woff2" docs/fonts/overpass-latin.woff2
install -m 0644 "$ASSET_DIR/fonts/jetbrains-mono-latin.woff2" docs/fonts/jetbrains-mono-latin.woff2
install -m 0644 "$AUDIT_DIR/LICENSE" LICENSE
install -m 0644 "$AUDIT_DIR/AUDIT_METHODOLOGY.md" AUDIT_METHODOLOGY.md
install -m 0644 "$ASSET_DIR/README.md" README.md

if [ -n "$(git status --porcelain -- LICENSE README.md AUDIT_METHODOLOGY.md docs/audit_data.json docs/index.html docs/app.js docs/styles.css docs/fonts)" ]; then
    git add LICENSE README.md AUDIT_METHODOLOGY.md docs/audit_data.json docs/index.html docs/app.js docs/styles.css docs/fonts
    git commit -m "Data update $(date -u +%Y-%m-%dT%H:%MZ)"
    git push origin main
    echo "Published."
    RESULT="nightly publish OK"
else
    echo "No change, nothing to push."
    RESULT="ran OK, no data change"
fi

# Promotion happens last.  The live site therefore never exposes a pending
# integration snapshot whose corresponding audit-site push failed.
cd "$AUDIT_DIR"
"$PY" audit_promote.py \
    --input "$PENDING_INTEGRATION" --output "$PUBLISHED_INTEGRATION"
notify ":white_check_mark: $PROJECT: $RESULT."
