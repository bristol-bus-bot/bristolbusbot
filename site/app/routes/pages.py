"""Page routes for the live map and vehicle profiles."""
from __future__ import annotations

import re

import math
from datetime import datetime

from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, current_app, jsonify, render_template

from .. import db
from ..services import localities as loc_svc
from ..services import buses as buses_svc
from ..services.route_pages import period_label as route_period_label

bp = Blueprint("pages", __name__)


def _display_service_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y%m%d")
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _display_post_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{parsed.day} {parsed.strftime('%b %Y, %H:%M')}"
    except (AttributeError, ValueError):
        return ""


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
    cfg = current_app.config["BBB"]
    return render_template(
        "index.html",
        audit_headline=audit.headline(),
        carto_basemap_key=cfg.carto_basemap_key,
        kofi_url=_kofi_url(cfg.kofi_url),
    )


def _kofi_url(value: str) -> str | None:
    """Only a plain https://ko-fi.com/<name> link is ever rendered."""
    return value if re.fullmatch(r"https://ko-fi\.com/[A-Za-z0-9_]{1,64}", value or "") else None


def _public_origin() -> str:
    return "https://" + current_app.config["BBB"].public_hosts[0]


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html", contact_email="hello@bristolbusbot.live")


@bp.route("/robots.txt")
def robots_txt():
    body = "\n".join((
        "User-agent: *",
        "Disallow: /api/",
        "Disallow: /assets/",
        "",
        f"Sitemap: {_public_origin()}/sitemap.xml",
        "",
    ))
    response = Response(body, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


# Route pages change once a night, so Cloudflare serves repeat visits.
ROUTE_PAGE_CACHE = "public, max-age=0, s-maxage=3600"


@bp.route("/routes")
def routes_index():
    pages = current_app.extensions["bbb_route_pages"]
    groups: list[dict] = []
    for entry in pages.routes():
        if not groups or groups[-1]["operator"] != entry["operator"]:
            groups.append({"operator": entry["operator"], "name": entry["operator_name"],
                           "routes": []})
        groups[-1]["routes"].append(entry)
    response = Response(render_template("routes.html", groups=groups))
    response.headers["Cache-Control"] = ROUTE_PAGE_CACHE
    return response


@bp.route("/routes/<operator_slug>/<route_slug>")
def route_page(operator_slug: str, route_slug: str):
    pages = current_app.extensions["bbb_route_pages"]
    view = pages.page(operator_slug, route_slug)
    if view is None:
        abort(404)
    audit = current_app.extensions["bbb_audit_integration"]
    vehicles = audit.vehicles_on_route(view["operator"], view["label"])
    response = Response(render_template(
        "route.html", route=view, vehicles=vehicles,
        period_label=route_period_label(str(view["period"].get("from") or ""),
                                        str(view["period"].get("to") or "")),
        audit_url="https://bristol-bus-bot.github.io/weca-bus-audit/",
    ))
    response.headers["Cache-Control"] = ROUTE_PAGE_CACHE
    return response


@bp.route("/sitemap.xml")
def sitemap_xml():
    """The homepage, the privacy page, every route page with figures, and every
    vehicle profile that loads now.

    Profiles come from the published audit integration, the same source the
    profile pages use, so the sitemap never lists a page that would 404.
    Routes without enough readings have a page but aren't listed."""
    origin = _public_origin()
    audit = current_app.extensions["bbb_audit_integration"]
    urls = [(f"{origin}/", None), (f"{origin}/privacy", None)]
    route_entries = current_app.extensions["bbb_route_pages"].routes()
    if route_entries:
        urls.append((f"{origin}/routes", None))
    for entry in route_entries:
        if entry["usable"]:
            lastmod = str(entry["period"].get("to") or "")
            lastmod = (f"{lastmod[:4]}-{lastmod[4:6]}-{lastmod[6:]}"
                       if re.fullmatch(r"\d{8}", lastmod) else None)
            urls.append((f"{origin}/routes/{entry['operator_slug']}/{entry['route_slug']}",
                         lastmod))
    for slug, through_date in audit.profile_slugs():
        lastmod = None
        if re.fullmatch(r"\d{8}", through_date):
            lastmod = f"{through_date[:4]}-{through_date[4:6]}-{through_date[6:]}"
        urls.append((f"{origin}/vehicles/{slug}", lastmod))
    entries = "".join(
        f"<url><loc>{escape(loc)}</loc>"
        + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
        + "</url>"
        for loc, lastmod in urls)
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>\n")
    response = Response(body, mimetype="application/xml")
    # Changes only when the audit is published, like the other static-ish data.
    response.headers["Cache-Control"] = "public, max-age=900"
    return response


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
        db.live(), fleet, stale_seconds=cfg.stale_vehicle_seconds, gtfs_conn=db.gtfs())
        if bus["vehicleRef"] == profile["vehicle_ref"]
        and bus["operatorRef"] == profile["operator"]), None)
    public_code = details.get("fleetNumber") or profile["vehicle_ref"].split("-")[-1]
    from ..services import record_words
    profile_view = {**profile, "delay_plot": _delay_plot(profile),
                    "plain_english": record_words.summary(profile)}
    profile_view["routes"] = []
    route_pages = current_app.extensions["bbb_route_pages"]
    for route in profile.get("routes", []):
        route_view = {
            **route,
            "delay_bins_s": profile.get("delay_bins_s"),
            "comparison_text": record_words.route_comparison(route),
            "page": route_pages.path_for(profile["operator"], str(route.get("route") or "")),
        }
        route_view["delay_plot"] = _delay_plot(route_view, compact=True)
        profile_view["routes"].append(route_view)
    return render_template(
        "vehicle_profile.html", profile=profile_view, details=details,
        active=active, public_code=public_code,
        measurement_start_label=_display_service_date(profile["measurement_start"]),
        through_date_label=_display_service_date(profile["through_date"]),
        display_service_date=_display_service_date,
        display_post_time=_display_post_time,
        audit_url="https://bristol-bus-bot.github.io/weca-bus-audit/",
    )


@bp.route("/api/vehicle-profiles/<slug>")
def vehicle_profile_data(slug: str):
    """Return one fresh, publishable profile for the unified map sidebar."""
    audit = current_app.extensions["bbb_audit_integration"]
    profile = audit.profile(slug)
    if profile is None:
        abort(404)
    from ..services import record_words
    profile = {
        **profile, "plain_english": record_words.summary(profile),
        "routes": [{**route, "comparison_text": record_words.route_comparison(route)}
                   for route in profile.get("routes", [])],
    }
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
        cache["stops_locality_status"] = current_app.extensions[
            "bbb_localities"]
    return jsonify({
        "stops": cache["stops_locality"],
        "localities": cache["stops_locality_status"],
    })
