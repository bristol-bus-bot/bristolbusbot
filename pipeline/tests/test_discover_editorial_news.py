from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from discover_editorial_news import (
    NoNewsCandidate,
    build_requirements,
    render_context_with_news,
    select_candidate,
    source_id,
)


NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


def result(
    title,
    description,
    link,
    published="2026-07-22T08:00:00Z",
    news_format="press_release",
):
    return {
        "title": title,
        "description": description,
        "format": news_format,
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
    assert item["append_source_link"] is False
    assert item["expires_at"] == "2026-07-29T08:00:00Z"
    assert item["requirements"] == [{
        "label": "approved story subject",
        "alternatives": ["New bus funding announced"],
    }]


def test_builds_human_editable_requirements_for_material_figures_and_dates():
    requirements = build_requirements(
        "New bus fare cap",
        "A £100 million fund starts on 1 January 2027 with fares down 25%.",
    )
    assert requirements == [
        {
            "label": "approved story subject",
            "alternatives": ["New bus fare cap"],
        },
        {
            "label": "approved detail £100 million",
            "alternatives": ["£100 million", "£100m"],
        },
        {
            "label": "approved detail 1 January 2027",
            "alternatives": ["1 January 2027"],
        },
        {
            "label": "approved detail 25%",
            "alternatives": ["25%"],
        },
    ]


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


def test_ignores_recently_updated_guidance_and_selects_genuine_news():
    candidate = select_candidate(
        {"results": [
            result(
                "£3 national bus fare cap",
                "A list of routes in the existing scheme.",
                "/guidance/3-national-bus-fare-cap",
                news_format="detailed_guide",
            ),
            result(
                "A genuine new bus announcement",
                "A newly announced change to bus services.",
                "/government/news/genuine-new-bus-announcement",
            ),
        ]},
        {"facts": [], "occasions": [], "news": []},
        now=NOW,
    )
    assert candidate["title"] == "A genuine new bus announcement"


def test_appends_news_without_reformatting_existing_requirements():
    original = '''{
  "schema_version": 1,
  "updated_at": "2026-07-22T00:00:00Z",
  "facts": [
    {
      "requirements": [
        { "label": "compact", "alternatives": ["still compact"] }
      ]
    }
  ],
  "occasions": [],
  "news": [
    {
      "id": "existing"
    }
  ]
}
'''
    item = {
        "id": "new-story",
        "requirements": [{"label": "subject", "alternatives": ["story"]}],
    }

    rendered = render_context_with_news(
        original,
        updated_at="2026-07-23T00:00:00Z",
        item=item,
    )

    assert '{ "label": "compact", "alternatives": ["still compact"] }' in rendered
    assert '"updated_at": "2026-07-23T00:00:00Z"' in rendered
    assert json.loads(rendered)["news"][-1] == item

    crlf_rendered = render_context_with_news(
        original.replace("\n", "\r\n"),
        updated_at="2026-07-23T00:00:00Z",
        item=item,
    )
    assert "\r\n" in crlf_rendered
    assert "\n" not in crlf_rendered.replace("\r\n", "")


def test_workflow_reuses_one_branch_and_surfaces_pr_creation_failures():
    workflow = (PIPELINE.parent / ".github" / "workflows" /
                "editorial-news.yml").read_text(encoding="utf-8")

    assert "${GITHUB_RUN_ID}" not in workflow
    assert 'branch="automation/editorial-news-${STORY_ID}"' in workflow
    assert "--force-with-lease" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert 'gh pr list --state all --limit 100 --json body' in workflow
    assert '--search "editorial-source:' not in workflow
