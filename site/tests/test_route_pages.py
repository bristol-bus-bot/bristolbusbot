"""Route pages: one server-rendered page per route, from the audit files."""
import json
import re

from app.services.route_pages import RoutePages, slugify

from test_audit_integration import SLUG, install_snapshot


def route_row(route, **extra):
    row = {
        "route": route, "on_time_pct": 60.6, "readings_in_gate": 3717, "on_time": 2252,
        "early": 358, "late": 1108, "very_late": 212, "median_delay_s": 204,
        "expected_trips": 992, "observed_trips": 959, "coverage_withheld": None,
        "qualification": {"status": "indicative"},
        "description": "Frenchay to Temple Meads, via Horfield and Kingsdown",
    }
    row.update(extra)
    return row


def install_routes(app, tmp_path):
    folder = tmp_path / "audit_site"
    (folder / "hourly").mkdir(parents=True)
    (folder / "segments").mkdir()
    (folder / "audit_summary.json").write_text(json.dumps({
        "operators": [{"code": "ALL", "name": "WECA network"},
                      {"code": "FBRI", "name": "First Bristol"},
                      {"code": "LEMB", "name": "The Big Lemon"}],
        "period": {"from": "20261004", "to": "20261031", "by_operator": {
            "ALL": {"routes": [route_row("72")]},
            "FBRI": {"routes": [
                route_row("72"), route_row("75"),
                route_row("4 Bath", description="Weston to Bath City Centre"),
                route_row("N43", on_time_pct=None, qualification={"status": "unavailable"},
                          description=None),
            ]},
            "LEMB": {"routes": [route_row("20", description=None)]},
        }},
    }), encoding="utf-8")
    weekday = {str(h): [80, 10, 10] for h in range(6, 23)}
    weekday["18"] = [36, 7, 114]
    (folder / "hourly" / "FBRI.json").write_text(json.dumps({
        "window": {"from": "20261004", "to": "20261031"}, "min_readings": 30,
        "routes": {"72": {"weekday": weekday}}}), encoding="utf-8")
    (folder / "segments" / "FBRI.json").write_text(json.dumps({"routes": {"72": [{
        "kind": "tight", "from": "Cotham Hill", "to": "Baldwin Street",
        "from_place": "Kingsdown", "to_place": "Bristol City Centre",
        "timetable_min": 9.0, "typical_min": 13.2, "band": "pm_peak", "day_type": "weekday",
    }]}}), encoding="utf-8")
    app.extensions["bbb_route_pages"] = RoutePages(str(folder))


def test_slugs_are_plain_and_readable():
    assert slugify("4 Bath") == "4-bath"
    assert slugify("The Big Lemon") == "big-lemon"
    assert slugify("Swindon's Bus Company") == "swindons-bus-company"


def test_a_route_page_shows_its_figures_in_plain_words(app, client, tmp_path):
    install_routes(app, tmp_path)
    response = client.get("/routes/first/72")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<title>72 bus punctuality: Frenchay to Temple Meads · Bristol Buses LIVE</title>" in html
    assert html.count("<h1>") == 1 and "<h1>First 72</h1>" in html
    assert "?" not in re.search(r"<title>(.*?)</title>", html).group(1)
    assert "on time <strong>6 in 10</strong> times, and late 3 in 10" in html
    assert "959 of 992" in html and "+3 min" in html
    assert "(4 to 31 October)" in html
    assert "worst around 6pm" in html
    assert "Cotham Hill (Kingsdown) to Baldwin Street (Bristol City Centre)" in html
    assert '<link rel="canonical" href="https://bristolbuses.live/routes/first/72">' in html
    assert response.headers["Cache-Control"] == "public, max-age=0, s-maxage=3600"
    assert chr(0x2014) not in html  # no em-dashes


def test_shared_numbers_resolve_to_the_right_town(app, client, tmp_path):
    install_routes(app, tmp_path)
    html = client.get("/routes/first/4-bath").get_data(as_text=True)
    assert "<h1>First 4 Bath</h1>" in html and "Weston to Bath City Centre" in html


def test_a_route_without_enough_readings_says_so_but_isnt_in_the_sitemap(app, client, tmp_path):
    install_routes(app, tmp_path)
    response = client.get("/routes/first/n43")
    assert response.status_code == 200
    assert "Not enough readings yet" in response.get_data(as_text=True)
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/routes/first/n43" not in sitemap
    assert "<loc>https://bristolbuses.live/routes/first/72</loc><lastmod>2026-10-31</lastmod>" in sitemap
    assert "<loc>https://bristolbuses.live/routes</loc>" in sitemap


def test_unknown_routes_and_the_network_view_are_404(app, client, tmp_path):
    install_routes(app, tmp_path)
    assert client.get("/routes/first/999").status_code == 404
    assert client.get("/routes/weca-network/72").status_code == 404
    assert client.get("/routes/nobody/72").status_code == 404


def test_the_index_lists_every_route_by_operator(app, client, tmp_path):
    install_routes(app, tmp_path)
    html = client.get("/routes").get_data(as_text=True)
    assert html.index("First Bristol") < html.index("The Big Lemon")
    for path in ("/routes/first/72", "/routes/first/4-bath", "/routes/first/n43",
                 "/routes/big-lemon/20"):
        assert f'href="{path}"' in html
    assert "few readings" in html


def test_without_audit_files_there_are_no_route_pages(client):
    assert client.get("/routes/first/72").status_code == 404
    assert "/routes" not in client.get("/sitemap.xml").get_data(as_text=True)


def test_route_and_vehicle_pages_link_to_each_other(app, client, tmp_path):
    install_routes(app, tmp_path)
    install_snapshot(app, tmp_path)
    route_html = client.get("/routes/first/75").get_data(as_text=True)
    assert f'href="/vehicles/{SLUG}"' in route_html
    vehicle_html = client.get(f"/vehicles/{SLUG}").get_data(as_text=True)
    assert 'href="/routes/first/75"' in vehicle_html


def test_route_pages_keep_the_strict_script_policy(app, client, tmp_path):
    install_routes(app, tmp_path)
    response = client.get("/routes/first/72")
    csp = response.headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "<script>" not in response.get_data(as_text=True)
