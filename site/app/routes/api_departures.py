"""/api/departures/<stop_code> — the departure board's endpoint."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from .. import db
from ..services import departures as dep_svc

bp = Blueprint("api_departures", __name__)


@bp.route("/api/departures/<stop_code>")
def api_departures(stop_code: str):
    cfg = current_app.config["BBB"]
    result = dep_svc.departures_for_stop(
        db.live(), db.gtfs(), stop_code,
        stale_seconds=cfg.stale_vehicle_seconds)
    if result is None:
        return jsonify({"error": "Stop not found"}), 404
    return jsonify(result)
