from pathlib import Path

import pytest

from app import create_app
from app.config import Config


SITE_ROOT = Path(__file__).resolve().parent.parent


def test_page_passes_key_as_data_and_loads_self_hosted_basemap(app, client):
    key = app.config["BBB"].carto_basemap_key
    page = client.get("/").get_data(as_text=True)
    assert f'data-carto-basemap-key="{key}"' in page

    assert '/js/basemap.js' in page
    assert '/vendor/maplibre-gl-6.6.0/maplibre-gl.css' in page
    assert 'unpkg.com' not in page


def test_attribution_is_visible_and_linked():
    source = (SITE_ROOT / "static/js/basemap.js").read_text(encoding="utf-8")
    assert "https://www.openstreetmap.org/copyright" in source
    assert "https://carto.com/attributions" in source


def test_self_hosted_module_worker_graph(client, app):
    prefix = f'/assets/{app.extensions["bbb_asset_version"]}/vendor/'
    for name in ('maplibre-gl.mjs', 'maplibre-gl-shared.mjs', 'maplibre-gl-worker.mjs'):
        response = client.get(prefix + 'maplibre-gl-6.6.0/' + name)
        assert response.status_code == 200
        assert response.mimetype in ('text/javascript', 'application/javascript')
    assert client.get(prefix + 'maplibre-gl-leaflet-0.1.4/leaflet-maplibre-gl.js').status_code == 200


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
