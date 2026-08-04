#!/bin/sh
set -eu
PIPELINE="${BBB_PIPELINE_DIR:-$HOME/bristolbusbot/current/pipeline}"
BBB_FLEET_FILE=/var/lib/bristolbusbot/enrichment/fbribuses.json
export BBB_FLEET_FILE
PY="$PIPELINE/venv/bin/python3"
SERVICE_DATE="$(date -d yesterday +%Y%m%d)"

"$PY" "$PIPELINE/audit_rollup.py" "$SERVICE_DATE"
exec "$PY" "$PIPELINE/audit_integration.py" \
    --through "$SERVICE_DATE" \
    --bot-db /var/lib/bristolbusbot/bot/app_data.db \
    --output /var/lib/bristolbusbot/collector/audit_integration.pending.json
