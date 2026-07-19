"""Liveness + readiness. /livez = process up; /healthz = data flowing."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify

from .. import db

bp = Blueprint("health", __name__)


@bp.route("/livez")
def livez():
    return "ok", 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}


@bp.route("/healthz")
def healthz():
    checks: dict = {}
    overall = "ok"
    try:
        db.gtfs().execute("SELECT 1 FROM stops LIMIT 1").fetchone()
        checks["gtfs_db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["gtfs_db"] = f"fail: {e}"
        overall = "fail"
    try:
        row = db.live().execute(
            "SELECT last_success_at FROM poller_status WHERE name='siri_vm'"
        ).fetchone()
        if row and row["last_success_at"]:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(row["last_success_at"])).total_seconds()
            checks["siri_age_seconds"] = round(age)
            if age > 300:
                checks["siri"] = "stale"
                overall = "fail"
            elif age > 120:
                checks["siri"] = "stale"
                overall = "warn" if overall == "ok" else overall
            else:
                checks["siri"] = "ok"
        else:
            checks["siri"] = "no successful poll yet"
            overall = "fail"
    except Exception as e:  # noqa: BLE001
        checks["siri"] = f"fail: {e}"
        overall = "fail"
    return (jsonify({"status": overall, "checks": checks}),
            200 if overall != "fail" else 503, {"Cache-Control": "no-store"})
