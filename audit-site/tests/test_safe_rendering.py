import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AttributeCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, _tag, attrs):
        self.attributes.extend(attrs)


def test_static_html_has_no_inline_event_handlers():
    parser = AttributeCollector()
    parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    assert not [name for name, _ in parser.attributes if name.lower().startswith("on")]


def test_dynamic_text_is_escaped_before_inner_html_rendering():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    required_guards = (
        "const route = escapeHTML(row.route)",
        "${escapeHTML(number)}",
        "${escapeHTML(operator)}",
        "const key = escapeHTML(row.key)",
        "${escapeHTML(o.code)}",
        "${escapeHTML(o.name)}",
        "${escapeHTML(m.model)}",
        "${escapeHTML(x.title)}",
        "${escapeHTML(x.text)}",
        "${escapeHTML(AREA_LABELS[area.name] || area.name)}",
    )
    assert all(guard in source for guard in required_guards)
    assert "line.innerHTML" not in source


def test_current_target_year_and_source_drive_every_comparison():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "finiteNumber(data.current_target_pct" in source
    assert "data.current_target_financial_year" in source
    assert "data.current_target_source_url" in source
    assert "safeHTTPURL(state.targetSourceUrl)" in source
    assert "escapeHTML(state.targetSource" in source
    for marker in (
            'id="headline-target-note"',
            'id="geo-target-note"',
            'id="route-target-note"',
            'id="historical-data-note"',
            'id="point-callout"',
            'id="footer-intro"'):
        assert marker in html


def test_historical_restatement_and_excluded_day_are_disclosed():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    methodology = (ROOT / "AUDIT_METHODOLOGY.md").read_text(encoding="utf-8")

    assert "contradictory evidence and are excluded" in html
    assert "Method and timetable changes" in html
    assert "That service day is excluded from all public results" in methodology
    assert "not a verified deployment" in methodology


def test_accessibility_landmarks_and_live_regions_are_present():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for marker in ('href="#main-content"', "<main", '<label class="sr-only" for="operator">',
                   'role="alert"', 'aria-live="polite"'):
        assert marker in html


def test_hourly_strips_escape_text_and_only_load_local_files():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    assert 'title="${escapeHTML(text)}"' in source
    assert 'data-detail="${escapeHTML(text)}"' in source
    assert 'aria-label="${escapeHTML(title)}"' in source
    # The hourly file name comes from audit_data.json; only hourly/<code>.json
    # is fetched, never an arbitrary path or URL.
    assert r"/^hourly\/[A-Za-z0-9_-]+\.json$/.test(file)" in source
    # Details are set as text, not HTML.
    assert "detail.textContent = cell.dataset.detail" in source


def test_timetable_fit_escapes_stop_names_and_only_loads_local_files():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    assert "${stopHTML(item.from, item.from_place)} → ${stopHTML(item.to, item.to_place)}" in source
    # Stop names and their towns are escaped before they reach innerHTML.
    assert "? `${escapeHTML(name)} <span class=\"place\">(${escapeHTML(place)})</span>`" in source
    assert ": escapeHTML(name);" in source
    assert "${escapeHTML(splitRoute(item.route)[0])}" in source
    assert r"/^segments\/[A-Za-z0-9_-]+\.json$/.test(file)" in source


def test_page_loads_the_small_summary_and_escapes_period_labels():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    assert 'fetchJSON("audit_summary.json")' in source
    assert "${escapeHTML(label)}" in source


def test_operator_picker_sits_in_the_bar_that_stays_on_screen():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    nav = html[html.index('<nav class="jump"'):html.index("</nav>")]
    assert '<select id="operator"' in nav


def test_map_only_loads_the_local_places_file():
    source = (ROOT / "app.js").read_text(encoding="utf-8")
    assert 'fetch("places.json")' in source
    assert "https://" not in source.split("function loadPlaces", 1)[1].split("}", 1)[0]
    data = json.loads((ROOT / "places.json").read_text(encoding="utf-8"))
    assert {a["name"] for a in data["areas"]} == {
        "Bath and North East Somerset", "Bristol", "North Somerset", "South Gloucestershire"}
    assert len(data["wards"]) > 100


def test_places_file_matches_its_sources():
    spec = importlib.util.spec_from_file_location("build_places", ROOT / "build_places.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert json.loads((ROOT / "places.json").read_text(encoding="utf-8")) == module.build()


def test_nightly_publish_copies_the_places_file():
    script = (ROOT.parent / "deploy" / "publish_to_github.sh").read_text(encoding="utf-8")
    assert 'install -m 0644 "$ASSET_DIR/places.json" docs/places.json' in script
    assert script.count("docs/places.json") >= 3


def test_published_files_use_unix_line_endings():
    # The Pi runs these; a Windows line ending breaks the script's first line.
    for path in (ROOT.parent / "deploy" / "publish_to_github.sh", ROOT / "places.json"):
        assert b"\r" not in path.read_bytes(), path.name


def test_page_asks_for_the_script_and_styles_by_version():
    # GitHub Pages lets browsers keep files for ten minutes. Versioned links
    # stop a new page running with an old script or stylesheet.
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT.parent / "deploy" / "publish_to_github.sh").read_text(encoding="utf-8")
    assert 'href="styles.css?v=__ASSET_VERSION__"' in html
    assert 'src="app.js?v=__ASSET_VERSION__"' in html
    assert 'sed "s/__ASSET_VERSION__/$ASSET_VERSION/g" "$ASSET_DIR/index.html" > docs/index.html' in script


def test_audit_site_has_search_basics_and_publishes_them():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<meta name="description"' in html
    assert '<meta property="og:title"' in html
    # Google Search Console ownership (URL-prefix property for the audit site).
    assert '<meta name="google-site-verification" content="jJreNS1rfgwbymtiuRrzHCfHi8ZP4BG9JeubNYaZslE">' in html
    assert '<link rel="canonical" href="https://bristol-bus-bot.github.io/weca-bus-audit/">' in html
    assert "Sitemap: https://bristol-bus-bot.github.io/weca-bus-audit/sitemap.xml" in (
        ROOT / "robots.txt").read_text(encoding="utf-8")
    assert "<loc>https://bristol-bus-bot.github.io/weca-bus-audit/</loc>" in (
        ROOT / "sitemap.xml").read_text(encoding="utf-8")
    assert (ROOT / "og-card.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    script = (ROOT.parent / "deploy" / "publish_to_github.sh").read_text(encoding="utf-8")
    for name in ("robots.txt", "sitemap.xml", "og-card.png"):
        assert f'install -m 0644 "$ASSET_DIR/{name}" docs/{name}' in script
        assert script.count(f"docs/{name}") >= 3
