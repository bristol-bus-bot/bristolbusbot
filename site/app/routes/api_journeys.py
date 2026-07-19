"""/api/journey-schedule/<code> — the clicked-bus route drawing."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from .. import db
from ..services import journeys as j_svc
from ..services import localities as loc_svc

bp = Blueprint("api_journeys", __name__)


@bp.route("/api/journey-schedule/<journey_code>")
def api_journey_schedule(journey_code: str):
    cache = current_app.extensions.setdefault("bbb_cache", {})
    if "stops_locality_map" not in cache:
        cfg = current_app.config["BBB"]
        entries = cache.get("stops_locality") or loc_svc.stops_with_locality(
            db.gtfs(), cfg.localities_json, cfg.enrichment_json)
        cache["stops_locality"] = entries
        cache["stops_locality_map"] = {e["stop_code"]: e for e in entries}
    result = j_svc.journey_schedule(
        db.gtfs(), journey_code,
        trip_id=request.args.get("tripId", ""),
        operator=request.args.get("operator", ""),
        line=request.args.get("line", ""),
        direction_ref=request.args.get("directionRef", ""),
        origin_aimed_dep=request.args.get("originAimedDep", ""),
        locality_map=cache["stops_locality_map"])
    if result is None:
        return jsonify({"error": "Journey not found"}), 404
    return jsonify(result)
