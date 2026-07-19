"""Flask application setup."""
from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from flask import Flask, abort, redirect, request, send_from_directory, url_for

from .config import Config
from . import db


def _static_asset_version(static_folder: str) -> str:
    """Return a content fingerprint for the browser asset tree."""
    root = Path(static_folder)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def create_app(config: Config | None = None) -> Flask:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    app = Flask(__name__, template_folder="../templates",
                static_folder="../static")
    app.config["BBB"] = config or Config()
    app.teardown_appcontext(db.close_all)
    asset_version = _static_asset_version(app.static_folder)
    app.extensions["bbb_asset_version"] = asset_version

    @app.get("/assets/<version>/<path:filename>")
    def versioned_static(version: str, filename: str):
        if not hmac.compare_digest(version, asset_version):
            abort(404)
        return send_from_directory(
            app.static_folder, filename, max_age=31536000, conditional=True)

    def asset_url(filename: str) -> str:
        return url_for(
            "versioned_static", version=asset_version, filename=filename)

    app.jinja_env.globals["asset_url"] = asset_url

    from .services.fleet import Fleet
    cfg = app.config["BBB"]
    app.extensions["bbb_fleet"] = Fleet(cfg.fleet_json, cfg.descriptions_json,
                                        cfg.waiting_json,
                                        cfg.depot_descriptions_json)
    from .services.audit_integration import AuditIntegration
    app.extensions["bbb_audit_integration"] = AuditIntegration(
        cfg.audit_integration_json, cfg.audit_max_age_seconds)

    from .routes import register_blueprints
    register_blueprints(app)

    @app.before_request
    def enforce_public_https():
        # Cloudflare supplies the original client scheme. Direct localhost
        # health checks have no forwarded header and must remain available.
        forwarded = request.headers.get("X-Forwarded-Proto")
        if not cfg.enforce_https or not forwarded:
            return None
        host = request.host.split(":", 1)[0].lower()
        if host not in cfg.public_hosts:
            abort(400)
        if forwarded.split(",", 1)[0].strip().lower() != "https":
            return redirect(
                f"https://{request.host}{request.full_path.rstrip('?')}",
                code=308,
            )
        return None

    @app.after_request
    def security_headers(response):
        if request.path.startswith(
                ("/assets/", "/static/fonts/", "/static/vendor/")):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), payment=(), geolocation=(self)")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join((
                "default-src 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "frame-ancestors 'none'",
                "form-action 'none'",
                "script-src 'self'",
                "style-src 'self' 'unsafe-inline'",
                "font-src 'self'",
                "img-src 'self' data: blob: https://*.basemaps.cartocdn.com",
                "connect-src 'self'",
                "manifest-src 'self'",
                "upgrade-insecure-requests",
            )))
        return response

    return app
