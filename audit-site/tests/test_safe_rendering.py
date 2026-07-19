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
        "${escapeHTML(row.route)}",
        "const key = escapeHTML(row.key)",
        "${escapeHTML(o.code)}",
        "${escapeHTML(o.name)}",
        "${escapeHTML(m.model)}",
        "${escapeHTML(r[0])}",
    )
    assert all(guard in source for guard in required_guards)
    assert "line.innerHTML" not in source


def test_accessibility_landmarks_and_live_regions_are_present():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for marker in ('href="#main-content"', "<main", 'role="tablist"',
                   'role="alert"', 'aria-live="polite"'):
        assert marker in html
