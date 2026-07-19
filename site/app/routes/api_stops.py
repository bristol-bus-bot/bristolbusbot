"""/api/stops (map markers) and /api/scheduled-departures/<code> (board
fallback). Stops are computed once and cached for the process lifetime —
the timetable changes monthly, not per request."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from .. import db
from ..services import stops as stops_svc

bp = Blueprint("api_stops", __name__)


@bp.route("/api/stops")
def api_stops():
    cache = current_app.extensions.setdefault("bbb_cache", {})
    if "stops" not in cache:
        cache["stops"] = stops_svc.all_stops(db.gtfs())
    return jsonify({"stops": cache["stops"]})


@bp.route("/api/scheduled-departures/<stop_code>")
def api_scheduled_departures(stop_code: str):
    result = stops_svc.scheduled_departures(db.gtfs(), stop_code)
    if result is None:
        return jsonify({"error": "Stop not found"}), 404
    return jsonify(result)
