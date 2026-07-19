"""Page routes for the live map and vehicle profiles."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, current_app, jsonify, render_template

from .. import db
from ..services import localities as loc_svc
from ..services import buses as buses_svc

bp = Blueprint("pages", __name__)


def _display_service_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y%m%d")
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


@bp.route("/")
def index():
    audit = current_app.extensions["bbb_audit_integration"]
    return render_template("index.html", audit_headline=audit.headline())


@bp.route("/vehicles/<slug>")
def vehicle_profile(slug: str):
    audit = current_app.extensions["bbb_audit_integration"]
    profile = audit.profile(slug)
    if profile is None:
        abort(404)
    fleet = current_app.extensions["bbb_fleet"]
    details = fleet.details(profile["vehicle_ref"], profile["operator"])
    cfg = current_app.config["BBB"]
    active = next((bus for bus in buses_svc.active_buses(
        db.live(), fleet, stale_seconds=cfg.stale_vehicle_seconds)
        if bus["vehicleRef"] == profile["vehicle_ref"]
        and bus["operatorRef"] == profile["operator"]), None)
    public_code = details.get("fleetNumber") or profile["vehicle_ref"].split("-")[-1]
    return render_template(
        "vehicle_profile.html", profile=profile, details=details,
        active=active, public_code=public_code,
        measurement_start_label=_display_service_date(profile["measurement_start"]),
        through_date_label=_display_service_date(profile["through_date"]),
        audit_url="https://bristol-bus-bot.github.io/weca-bus-audit/",
    )


@bp.route("/api/stops-with-locality")
def api_stops_with_locality():
    cache = current_app.extensions.setdefault("bbb_cache", {})
    if "stops_locality" not in cache:
        cfg = current_app.config["BBB"]
        cache["stops_locality"] = loc_svc.stops_with_locality(
            db.gtfs(), cfg.localities_json, cfg.enrichment_json)
    return jsonify({"stops": cache["stops_locality"]})
