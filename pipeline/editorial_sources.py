"""Read official local RSS feeds as untrusted research material, never approvals."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = {
    "https://www.westofengland-ca.gov.uk/feed/?post_type=news": "West of England Mayoral Combined Authority",
    "https://news.firstbus.co.uk/feed/rss": "First Bus",
}
MAX_BYTES = 2 * 1024 * 1024
LOCAL_RE = re.compile(
    r"\b(Bristol|Bath|Weston(?:-super-Mare)?|South Gloucestershire|"
    r"West of England|North Somerset|Thornbury|Yate|Keynsham)\b", re.I)
NATIONAL_RE = re.compile(
    r"\b(England|nationwide|national bus|across the country|across Britain|across the UK)\b", re.I)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def relevant_result(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('description', '')}"
    # The local authority feed covers our area; it still needs a bus subject.
    if item.get("publisher") == FEEDS["https://www.westofengland-ca.gov.uk/feed/?post_type=news"]:
        return True
    if LOCAL_RE.search(text):
        return True
    # National government policy is useful; other operators' regional PR is not.
    return not item.get("source_url") and bool(NATIONAL_RE.search(text))


def parse_feed(raw: bytes, feed_url: str) -> list[dict]:
    if feed_url not in FEEDS or not raw or len(raw) > MAX_BYTES:
        raise ValueError("unsupported or oversized editorial feed")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("editorial feeds must not contain XML declarations of entities")
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("editorial source is not an RSS feed")
    results = []
    for item in root.findall("./channel/item")[:100]:
        url = (item.findtext("link") or "").strip()
        if any(character.isspace() for character in url):
            continue
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != urllib.parse.urlsplit(feed_url).netloc
                or parsed.username or parsed.password):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        results.append({
            "title": plain_text(item.findtext("title") or ""),
            "description": plain_text(item.findtext("description") or ""),
            "link": url,
            "source_url": url,
            "publisher": FEEDS[feed_url],
            "format": "press_release",
            "public_timestamp": published.isoformat(),
        })
    return results


def fetch_feed(url: str, opener=None) -> list[dict]:
    if url not in FEEDS:
        raise ValueError("unsupported editorial feed")
    opener = opener or urllib.request.build_opener()
    request = urllib.request.Request(url, headers={
        "Accept": "application/rss+xml, application/xml",
        "User-Agent": "bristolbusbot-editorial-discovery/1",
    })
    with opener.open(request, timeout=20) as response:
        if response.geturl().rstrip("/") != url.rstrip("/"):
            raise ValueError("editorial feed redirected to an unexpected endpoint")
        return parse_feed(response.read(MAX_BYTES + 1), url)
