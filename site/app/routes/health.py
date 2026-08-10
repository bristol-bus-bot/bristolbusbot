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
    fleet = current_app.extensions["bbb_fleet"].status
    checks["fleet"] = fleet
    if not fleet.get("loaded") or not fleet.get("records"):
        overall = "fail"
    descriptions = fleet.get("descriptions")
    descriptions = descriptions if isinstance(descriptions, dict) else {}
    if set(descriptions) != {"in_service", "waiting", "depot"} or any(
            not value.get("loaded") or not value.get("records")
            for value in descriptions.values()
            if isinstance(value, dict)) or any(
                not isinstance(value, dict) for value in descriptions.values()):
        overall = "fail"
    localities = current_app.extensions["bbb_localities"]
    checks["localities"] = localities
    if not localities.get("loaded") or not localities.get("records"):
        overall = "fail"
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
