from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


SOCIAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOCIAL))

import threads_candidates as candidates  # noqa: E402


def row(*, post_uri: str, timestamp: str, line: str = "75",
        significance: int = 8, delay: int = 600,
        low_confidence: int = 0) -> dict:
    return {
        "operator_ref": "FBRI", "vehicle_ref": "FBRI-100",
        "line": line, "journey_ref": f"journey-{post_uri}",
        "origin_aimed_departure": "12:00:00", "delay_seconds": delay,
        "low_confidence": low_confidence, "post_content": f"Post {post_uri}",
        "post_type": "delay", "significance_score": significance,
        "timestamp": timestamp, "post_uri": f"at://did/post/{post_uri}",
    }


def test_selector_uses_significance_budget_confidence_and_route_cooldown():
    posts = [
        row(post_uri="best", timestamp="2026-07-28T07:30:00Z", significance=10),
        row(post_uri="same-route", timestamp="2026-07-28T08:00:00Z", significance=8),
        row(post_uri="other-route", timestamp="2026-07-28T08:05:00Z", line="76", significance=9),
        row(post_uri="low", timestamp="2026-07-28T09:00:00Z", line="77", significance=3),
        row(post_uri="uncertain", timestamp="2026-07-28T10:00:00Z", line="78", low_confidence=1),
    ]
    report = candidates.select(posts, candidates.Rules(daily_budget=2))
    chosen = [item for item in report["decisions"] if item["decision"] == "candidate"]
    assert [item["route"] for item in chosen] == ["75", "76"]
    reasons = " ".join(item["reason"] for item in report["decisions"])
    assert "same operator and route" in reasons
    assert "significance below" in reasons
    assert "low-confidence" in reasons
    assert report["mode"] == "shadow-only"


def test_overnight_requires_a_genuinely_severe_delay():
    report = candidates.select([
        row(post_uri="mild", timestamp="2026-07-28T02:00:00Z", delay=900),
        row(post_uri="severe", timestamp="2026-07-28T03:00:00Z", line="76", delay=1500),
    ], candidates.Rules())
    assert [item["decision"] for item in report["decisions"]] == ["rejected", "candidate"]


def test_database_is_opened_read_only_and_requires_provenance(tmp_path):
    path = tmp_path / "app_data.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE engagement_analytics (id INTEGER PRIMARY KEY, post_uri TEXT)")
    db.commit()
    db.close()
    try:
        candidates.read_posts(path, datetime(2026, 7, 28, tzinfo=timezone.utc))
    except RuntimeError as exc:
        assert "predates social provenance" in str(exc)
    else:
        raise AssertionError("legacy schema should fail explicitly")
