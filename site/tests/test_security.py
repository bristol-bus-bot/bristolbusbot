import re


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "fonts.googleapis.com" not in csp
    assert "fonts.gstatic.com" not in csp
    assert "unpkg.com" not in csp
    assert "https://*.basemaps.cartocdn.com" in csp


def test_rendered_page_has_no_third_party_fonts_or_libraries(client):
    page = client.get("/").get_data(as_text=True)
    for forbidden in ("fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com"):
        assert forbidden not in page
    assert re.search(
        r"/assets/[0-9a-f]{12}/vendor/leaflet-1\.9\.4/leaflet\.", page)
    assert re.search(r"/assets/[0-9a-f]{12}/css/fonts\.css", page)
    assert "/static/js/" not in page
    assert "/static/css/" not in page


def test_content_versioned_asset_graph(client, app):
    version = app.extensions["bbb_asset_version"]
    response = client.get(f"/assets/{version}/js/search_logic.js")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == (
        "public, max-age=31536000, immutable")
    assert client.get("/assets/not-this-release/js/search_logic.js").status_code == 404

    # Relative module imports inherit the release path automatically.
    response = client.get(f"/assets/{version}/js/search.js")
    assert b'from "./util.js"' in response.data
    assert client.get(f"/assets/{version}/js/util.js").status_code == 200


def test_versioned_static_assets_are_immutable(client):
    for path in (
        "/static/fonts/overpass-variable.0a1c9727.woff2",
        "/static/fonts/jetbrains-mono-variable.83c005d4.woff2",
        "/static/vendor/leaflet-1.9.4/leaflet.a7837102.css",
        "/static/vendor/leaflet-1.9.4/leaflet.85d455b4.js",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == (
            "public, max-age=31536000, immutable")


def test_forwarded_http_redirects_to_https(app, client):
    app.config["BBB"].enforce_https = True
    try:
        response = client.get(
            "/api/buses?test=1",
            headers={"Host": "bristolbuses.live", "X-Forwarded-Proto": "http"},
        )
    finally:
        app.config["BBB"].enforce_https = False
    assert response.status_code == 308
    assert response.headers["Location"] == "https://bristolbuses.live/api/buses?test=1"


def test_https_enforcement_rejects_untrusted_forwarded_host(app, client):
    app.config["BBB"].enforce_https = True
    try:
        response = client.get(
            "/", headers={"Host": "evil.example", "X-Forwarded-Proto": "http"})
    finally:
        app.config["BBB"].enforce_https = False
    assert response.status_code == 400
