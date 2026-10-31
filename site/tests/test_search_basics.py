"""Search basics (planning #90): what a search engine reads on each page."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from test_audit_integration import SLUG, install_snapshot

ORIGIN = "https://bristolbuses.live"


class Page(HTMLParser):
    """Collects headings, meta tags, links and the visible body text."""

    def __init__(self):
        super().__init__()
        self.h1 = 0
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        if tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key:
                self.meta[key] = attrs.get("content") or ""
        if tag == "link" and attrs.get("rel"):
            self.links[attrs["rel"]] = attrs.get("href") or ""
        if tag in ("script", "style", "head", "title"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def parse(response) -> Page:
    page = Page()
    page.feed(response.get_data(as_text=True))
    return page


def test_homepage_has_one_heading_a_description_and_preview_tags(client):
    response = client.get("/")
    page = parse(response)
    assert page.h1 == 1
    assert 50 < len(page.meta["description"]) <= 160
    assert "Bristol Bus Bot" in page.meta["description"]
    assert page.meta["og:title"]
    assert page.meta["og:url"] == f"{ORIGIN}/"
    assert page.meta["og:image"].startswith(f"{ORIGIN}/assets/")
    assert page.meta["og:image"].endswith("/social/og-card.png")
    assert page.links["canonical"] == f"{ORIGIN}/"
    image = client.get(page.meta["og:image"].removeprefix(ORIGIN))
    assert image.status_code == 200 and image.mimetype == "image/png"


def test_both_names_are_in_visible_text_not_only_meta_tags(client):
    text = " ".join(parse(client.get("/")).text)
    assert "Bristol Bus Bot" in text
    assert "punctuality audit" in text


def test_about_button_opens_the_server_rendered_panel(client):
    html = client.get("/").get_data(as_text=True)
    header = html[html.index("<header"):html.index("</header>")]
    assert 'popovertarget="about-panel"' in header
    social = header[header.index('class="social-links"'):]
    assert social.index('id="coffee-link"') < social.index('id="about-link"') if 'id="coffee-link"' in social \
        else social.index('id="github-header-link"') < social.index('id="about-link"')
    panel = html[html.index('<div id="about-panel" popover'):]
    panel = panel[:panel.index("</div>")]
    assert "<h2>About this map</h2>" in panel
    for link in ("https://bristol-bus-bot.github.io/weca-bus-audit/",
                 "https://bsky.app/profile/did:plc:y2oqhgnbw66jtd5hnbda76nk",
                 "https://www.bus-data.dft.gov.uk/", "https://bustimes.org/",
                 "https://github.com/bristol-bus-bot/bristolbusbot", "/privacy"):
        assert f'href="{link}"' in panel


def test_robots_txt_blocks_the_api_and_points_at_the_sitemap(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    body = response.get_data(as_text=True)
    assert "Disallow: /api/" in body
    assert f"Sitemap: {ORIGIN}/sitemap.xml" in body


def test_sitemap_lists_only_pages_that_load(app, client, tmp_path):
    install_snapshot(app, tmp_path)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.mimetype == "application/xml"
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(response.data)
    locations = [url.findtext("s:loc", namespaces=ns) for url in root.findall("s:url", ns)]
    assert f"{ORIGIN}/" in locations
    assert f"{ORIGIN}/privacy" in locations
    assert f"{ORIGIN}/vehicles/{SLUG}" in locations
    lastmods = [url.findtext("s:lastmod", namespaces=ns) for url in root.findall("s:url", ns)]
    assert "2026-07-16" in lastmods
    for location in locations:
        assert client.get(location.removeprefix(ORIGIN)).status_code == 200, location


def test_sitemap_without_an_audit_still_lists_the_fixed_pages(client):
    root = ET.fromstring(client.get("/sitemap.xml").data)
    assert len(root) == 2


def test_vehicle_page_states_its_own_address_and_description(app, client, tmp_path):
    install_snapshot(app, tmp_path)
    page = parse(client.get(f"/vehicles/{SLUG}"))
    assert page.links["canonical"] == f"{ORIGIN}/vehicles/{SLUG}"
    assert page.meta["og:url"] == f"{ORIGIN}/vehicles/{SLUG}"
    assert "66.7% on time" in page.meta["description"]
    assert "First Bristol" in page.meta["og:description"]


def test_privacy_page_has_its_own_title_description_and_contact(client):
    response = client.get("/privacy")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<title>Privacy · Bristol Buses LIVE</title>" in html
    page = parse(response)
    assert page.h1 == 1
    assert page.links["canonical"] == f"{ORIGIN}/privacy"
    assert "cookies" in page.meta["description"]
    assert 'href="mailto:bristolbusbot@gmail.com"' in html
    assert 'id="privacy-link"' in client.get("/").get_data(as_text=True)


def test_structured_data_names_both_brands_without_breaking_the_csp(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    import json
    data = json.loads(block)
    assert data["name"] == "Bristol Buses LIVE"
    assert data["alternateName"] == "Bristol Bus Bot"
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"].split("script-src")[1].split(";")[0]
