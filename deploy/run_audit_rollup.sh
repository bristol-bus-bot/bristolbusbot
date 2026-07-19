#!/bin/sh
set -eu
PIPELINE="${BBB_PIPELINE_DIR:-$HOME/bristolbusbot/current/pipeline}"
PY="$PIPELINE/venv/bin/python3"
SERVICE_DATE="$(date -d yesterday +%Y%m%d)"

"$PY" "$PIPELINE/audit_rollup.py" "$SERVICE_DATE"
exec "$PY" "$PIPELINE/audit_integration.py" \
    --through "$SERVICE_DATE" \
    --output /var/lib/bristolbusbot/collector/audit_integration.pending.json
