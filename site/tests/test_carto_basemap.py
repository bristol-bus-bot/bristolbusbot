from pathlib import Path

import pytest

from app import create_app
from app.config import Config


SITE_ROOT = Path(__file__).resolve().parent.parent


def test_page_passes_key_as_data_and_javascript_keys_both_themes(app, client):
    key = app.config["BBB"].carto_basemap_key
    page = client.get("/").get_data(as_text=True)
    assert f'data-carto-basemap-key="{key}"' in page

    source = (SITE_ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "rastertiles/voyager/{z}/{x}/{y}{r}.png" in source
    assert "dark_all/{z}/{x}/{y}{r}.png" in source
    assert "?key=${encodeURIComponent(key)}" in source
    assert "tileLayer.setUrl(tileUrl(theme))" in source


def test_attribution_is_visible_and_linked():
    source = (SITE_ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "https://www.openstreetmap.org/copyright" in source
    assert "https://carto.com/attributions" in source


def test_production_refuses_missing_or_malformed_key_without_echoing_value():
    with pytest.raises(RuntimeError, match="not configured"):
        create_app(Config(enforce_https=True, carto_basemap_key=""))

    invalid = "do not print this CARTO key"
    with pytest.raises(RuntimeError) as caught:
        create_app(Config(enforce_https=True, carto_basemap_key=invalid))
    assert invalid not in str(caught.value)
    assert "value hidden" in str(caught.value)


def test_health_reports_presence_without_exposing_key(app, client):
    key = app.config["BBB"].carto_basemap_key
    body = client.get("/healthz").get_data(as_text=True)
    assert '"carto_basemap_key":"configured"' in body
    assert key not in body
