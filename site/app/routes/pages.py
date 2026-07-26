"""Page routes for the live map and vehicle profiles."""
from __future__ import annotations

import math
from datetime import datetime

from flask import Blueprint, abort, current_app, jsonify, render_template

from .. import db
from ..services import localities as loc_svc
from ..services import buses as buses_svc

bp = Blueprint("pages", __name__)


def _display_service_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y%m%d")
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _delay_plot(profile: dict, *, compact: bool = False) -> dict | None:
    try:
        edges = [int(value) for value in profile.get("delay_bins_s", [])]
        counts = [int(value) for value in profile.get("delay_counts", [])]
    except (TypeError, ValueError):
        return None
    if (len(edges) < 2 or len(counts) != len(edges) + 1
            or any(value < 0 for value in counts)
            or any(current <= previous
                   for previous, current in zip(edges, edges[1:]))):
        return None
    total = sum(counts)
    if not total:
        return None
    try:
        if int(profile.get("readings")) != total:
            return None
    except (TypeError, ValueError):
        return None
    minimum, maximum = edges[0], edges[-1]
    delay_range = maximum - minimum
    max_dots = 42 if compact else 900
    max_column = 3 if compact else 48
    unit = max(1, math.ceil(total / max_dots),
               math.ceil(max(counts) / max_column))

    def percentage(value: float) -> float:
        return round(max(0, min(100, 100 * (value - minimum) / delay_range)), 3)

    columns = []
    for index, count in enumerate(counts):
        if index == 0:
            centre = minimum
        elif index == len(edges):
            centre = maximum
        else:
            centre = (edges[index - 1] + edges[index]) / 2
        columns.append({
            "left_pct": percentage(centre),
            "dots": math.ceil(count / unit) if count else 0,
            "kind": "early" if centre < -60 else (
                "late" if centre >= 360 else "on-time"),
        })
    return {
        "total": total,
        "unit": unit,
        "minimum_minutes": round(minimum / 60),
        "maximum_minutes": round(maximum / 60),
        "zero_pct": percentage(0),
        "on_time_start_pct": percentage(-60),
        "on_time_width_pct": percentage(360) - percentage(-60),
        "columns": columns,
    }


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
    profile_view = {**profile, "delay_plot": _delay_plot(profile)}
    profile_view["routes"] = []
    for route in profile.get("routes", []):
        route_view = {
            **route,
            "delay_bins_s": profile.get("delay_bins_s"),
        }
        route_view["delay_plot"] = _delay_plot(route_view, compact=True)
        profile_view["routes"].append(route_view)
    return render_template(
        "vehicle_profile.html", profile=profile_view, details=details,
        active=active, public_code=public_code,
        measurement_start_label=_display_service_date(profile["measurement_start"]),
        through_date_label=_display_service_date(profile["through_date"]),
        display_service_date=_display_service_date,
        audit_url="https://bristol-bus-bot.github.io/weca-bus-audit/",
    )


@bp.route("/api/vehicle-profiles/<slug>")
def vehicle_profile_data(slug: str):
    """Return one fresh, publishable profile for the unified map sidebar."""
    audit = current_app.extensions["bbb_audit_integration"]
    profile = audit.profile(slug)
    if profile is None:
        abort(404)
    response = jsonify({"profile": profile})
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/stops-with-locality")
def api_stops_with_locality():
    cache = current_app.extensions.setdefault("bbb_cache", {})
    if "stops_locality" not in cache:
        cfg = current_app.config["BBB"]
        cache["stops_locality"] = loc_svc.stops_with_locality(
            db.gtfs(), cfg.localities_json, cfg.enrichment_json)
    return jsonify({"stops": cache["stops_locality"]})
