from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from discover_editorial_news import (
    NoNewsCandidate,
    select_candidate,
    source_id,
)


NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


def result(title, description, link, published="2026-07-22T08:00:00Z"):
    return {
        "title": title,
        "description": description,
        "link": link,
        "public_timestamp": published,
    }


def test_selects_recent_bus_story_and_builds_bounded_approval_item():
    candidate = select_candidate(
        {"results": [
            result(
                "New bus funding announced",
                "Funding will support local bus services in England.",
                "/government/news/new-bus-funding-announced",
            ),
        ]},
        {"facts": [], "occasions": [], "news": []},
        now=NOW,
    )
    item = candidate["item"]
    assert candidate["url"].startswith("https://www.gov.uk/")
    assert item["max_uses_total"] == 2
    assert item["cooldown_hours"] == 36
    assert item["append_source_link"] is True
    assert item["expires_at"] == "2026-07-29T08:00:00Z"


def test_skips_known_rejected_bee_and_stale_stories_before_next_candidate():
    rejected_url = "https://www.gov.uk/government/news/rejected-bus-story"
    search = {"results": [
        result(
            "Rejected bus story",
            "A bus story already closed in GitHub.",
            "/government/news/rejected-bus-story",
        ),
        result(
            "Bee Network buses",
            "A comparison the project has removed.",
            "/government/news/bee-network-buses",
        ),
        result(
            "Stale bus story",
            "An old bus announcement.",
            "/government/news/stale-bus-story",
            "2026-07-01T08:00:00Z",
        ),
        result(
            "Fresh coach safety guidance",
            "New safety guidance for coach operators.",
            "/government/news/fresh-coach-safety-guidance",
        ),
    ]}
    candidate = select_candidate(
        search,
        {"facts": [], "occasions": [], "news": []},
        now=NOW,
        excluded_source_ids={source_id(rejected_url)},
    )
    assert candidate["title"] == "Fresh coach safety guidance"


def test_raises_skip_outcome_when_no_relevant_story_exists():
    with pytest.raises(NoNewsCandidate):
        select_candidate(
            {"results": [
                result(
                    "Rail funding",
                    "An announcement about trains.",
                    "/government/news/rail-funding",
                ),
            ]},
            {"facts": [], "occasions": [], "news": []},
            now=NOW,
        )
