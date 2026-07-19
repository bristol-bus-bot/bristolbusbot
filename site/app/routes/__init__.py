"""Blueprint registration — the only place routes are wired up."""
from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from . import (api_buses, api_departures, api_journeys, api_misc,
                   api_stops, health, pages)
    app.register_blueprint(pages.bp)
    app.register_blueprint(health.bp)
    app.register_blueprint(api_buses.bp)
    app.register_blueprint(api_departures.bp)
    app.register_blueprint(api_stops.bp)
    app.register_blueprint(api_misc.bp)
    app.register_blueprint(api_journeys.bp)
