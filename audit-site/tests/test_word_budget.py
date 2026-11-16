"""Keep static page copy small."""
import re
from html.parser import HTMLParser
from pathlib import Path


class PageWords(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.excluded = 0
        self.stack = []
        self.words = []

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        attrs = dict(attrs)
        excluded = tag in {"script", "style"} or "hidden" in attrs or "sr-only" in attrs.get("class", "").split()
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, excluded))
            self.excluded += excluded

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.excluded -= self.stack.pop()[1]
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        # Closed details ARE counted: adding an explanatory paragraph there
        # must still force a review. Attributes and metadata are not copy.
        if self.in_body and not self.excluded:
            self.words.extend(re.findall(r"\b\w+(?:[’'–-]\w+)*\b", data))


def test_static_page_word_budget():
    parser = PageWords()
    parser.feed((Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8"))
    budget = 160
    assert len(parser.words) <= budget, (
        f"{len(parser.words)} static words: budget {budget}. "
        "Put longer background in README.md or AUDIT_METHODOLOGY.md."
    )
