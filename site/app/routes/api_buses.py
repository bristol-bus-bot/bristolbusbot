"""/api/buses — every active vehicle, in the frontend's established shape."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, url_for

from .. import db
from ..services import buses as buses_svc

bp = Blueprint("api_buses", __name__)


@bp.route("/api/buses")
def api_buses():
    cfg = current_app.config["BBB"]
    fleet = current_app.extensions["bbb_fleet"]
    payload = buses_svc.active_buses(db.live(), fleet,
                                     stale_seconds=cfg.stale_vehicle_seconds)
    audit = current_app.extensions["bbb_audit_integration"]
    for bus in payload:
        slug = audit.slug_for_vehicle(bus.get("operatorRef"),
                                      bus.get("vehicleRef"))
        if slug:
            bus["profileUrl"] = url_for("pages.vehicle_profile", slug=slug)
    _fill_stop_names(payload)
    resp = jsonify({"buses": payload, "count": len(payload)})
    # Clients poll faster than the collector writes, so roughly half of
    # all polls fetch identical data. An ETag turns those into bodyless
    # 304s; browsers handle the revalidation transparently.
    resp.add_etag()
    resp.headers["Cache-Control"] = "no-cache"
    return resp.make_conditional(request)


def _fill_stop_names(payload: list[dict]) -> None:
    """lastStopName carries a stop_code out of the collector; swap for the
    human name from GTFS in one query per request."""
    codes = {b["lastStopName"] for b in payload
             if b["lastStopName"] not in (None, "unknown")}
    if not codes:
        return
    q = ",".join("?" * len(codes))
    rows = db.gtfs().execute(
        f"SELECT stop_code, stop_name FROM stops WHERE stop_code IN ({q})",
        tuple(codes)).fetchall()
    names = {r["stop_code"]: r["stop_name"] for r in rows}
    for b in payload:
        b["lastStopName"] = names.get(b["lastStopName"], b["lastStopName"])
