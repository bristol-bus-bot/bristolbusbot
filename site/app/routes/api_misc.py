"""The small endpoints: fleet search, descriptions, route shapes, boundary,
busbot posts, and /api/situations (new — the collector's disruptions).
All static-ish payloads cached for the process lifetime."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify, url_for

from .. import db

bp = Blueprint("api_misc", __name__)
logger = logging.getLogger(__name__)

BUSBOT_HANDLE = "bristolbusbot.live"  # the bot's own-domain handle (launch 2026-07-13)


def _cache(key: str, build):
    cache = current_app.extensions.setdefault("bbb_cache", {})
    if key not in cache:
        cache[key] = build()
    return cache[key]


@bp.route("/api/fleet")
def api_fleet():
    def build():
        fleet = current_app.extensions["bbb_fleet"]
        slimmed = []
        for bus in fleet.raw_list:
            vt = bus.get("vehicle_type") or {}
            lv = bus.get("livery") or {}
            gr = bus.get("garage") or {}
            op = bus.get("operator") or {}
            sf_raw = bus.get("special_features")
            if isinstance(sf_raw, list):
                sf = [str(x).strip() for x in sf_raw if x]
            elif isinstance(sf_raw, str) and sf_raw.strip():
                sf = [x.strip() for x in sf_raw.split(",") if x.strip()]
            else:
                sf = []
            slimmed.append({
                "id": bus.get("id"),
                "fleet_number": bus.get("fleet_number"),
                "fleet_code": str(bus.get("fleet_code")
                                  or bus.get("fleet_number") or ""),
                "reg": (bus.get("reg") or "").upper(),
                "previous_reg": (bus.get("previous_reg") or "").upper(),
                "model": vt.get("name") or "",
                "fuel": vt.get("fuel") or "",
                "double_decker": bool(vt.get("double_decker", False)),
                "coach": bool(vt.get("coach", False)),
                "livery_name": lv.get("name") or "",
                "livery_left": lv.get("left") or "",
                "garage_name": gr.get("name") or "",
                "garage_code": gr.get("code") or "",
                "operator_id": op.get("id") or "",
                "operator_name": op.get("name") or "",
                "name": bus.get("name") or "",
                "notes": bus.get("notes") or "",
                "withdrawn": bool(bus.get("withdrawn", False)),
                "special_features": sf,
            })
        return slimmed
    audit = current_app.extensions["bbb_audit_integration"]
    result = []
    for base in _cache("fleet_search", build):
        bus = dict(base)
        slug = audit.slug_for_identity(bus.get("fleet_code"), bus.get("reg"))
        if slug:
            bus["profile_url"] = url_for("pages.vehicle_profile", slug=slug)
            bus["profile_api_url"] = url_for(
                "pages.vehicle_profile_data", slug=slug)
        result.append(bus)
    return jsonify({"fleet": result})


@bp.route("/api/bus-descriptions")
def api_bus_descriptions():
    def build():
        cfg = current_app.config["BBB"]
        def load(path):
            p = Path(path) if path else None
            if not p or not p.exists():
                return {}
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("descriptions load failed %s: %s", path, e)
                return {}
        return {"in_service": load(cfg.descriptions_json),
                "depot": load(cfg.depot_descriptions_json),
                "waiting": load(cfg.waiting_json)}
    return jsonify(_cache("descriptions", build))


@bp.route("/api/route-shapes")
def api_route_shapes():
    def build():
        g = db.gtfs()
        exists = g.execute("SELECT name FROM sqlite_master WHERE type='table'"
                           " AND name='route_shapes'").fetchone()
        if not exists:
            return {}
        cols = [r[1] for r in g.execute("PRAGMA table_info(route_shapes)")]
        has_variant = "variant" in cols
        sel = ("SELECT route_name, operator_noc, direction_id, variant, points_json"
               if has_variant else
               "SELECT route_name, operator_noc, direction_id, 0, points_json")
        shapes = {}
        for name, noc, direction, variant, points in g.execute(
                f"{sel} FROM route_shapes"):
            shapes[f"{noc}_{name}_{direction}_{variant}"] = {
                "route": name, "operator": noc, "direction": direction,
                "variant": variant, "points": json.loads(points)}
        return shapes
    return jsonify(_cache("route_shapes", build))


@bp.route("/api/boundary")
def api_boundary():
    def build():
        p = Path(current_app.config["BBB"].boundary_geojson)
        return json.loads(p.read_text()) if p.exists() else None
    data = _cache("boundary", build)
    if data is None:
        return jsonify({"error": "Boundary file not found"}), 404
    return jsonify(data)


@bp.route("/api/busbot-posts")
def api_busbot_posts():
    """Return the bot profile and an empty recent-post fallback."""
    return jsonify({"posts": [], "profileUrl":
                    f"https://bsky.app/profile/{BUSBOT_HANDLE}",
                    "handle": BUSBOT_HANDLE})


@bp.route("/api/situations")
def api_situations():
    """Active WECA disruptions from the collector (SIRI-SX)."""
    rows = db.live().execute(
        """SELECT situation_number, participant, progress, planned, reason,
                  summary, description, advice, severity, validity_start,
                  validity_end, versioned_at, link, affected_json
           FROM situations WHERE closed_at IS NULL
           ORDER BY versioned_at DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["affected"] = json.loads(d.pop("affected_json") or "{}")
        out.append(d)
    return jsonify({"situations": out, "count": len(out)})
