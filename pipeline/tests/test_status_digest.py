from datetime import datetime
import json
import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import status_digest


def test_bot_digest_counts_durable_success_rows_only(tmp_path, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    database = tmp_path / "app_data.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE engagement_analytics (timestamp TEXT, post_uri TEXT)")
    connection.executemany(
        "INSERT INTO engagement_analytics VALUES (?, ?)",
        ((f"{today}T08:00:00+01:00", "at://successful"),
         (f"{today}T08:05:00+01:00", None),
         ("2020-01-01T00:00:00+00:00", "at://old")),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(status_digest, "BOT_DB", database)
    assert "1 post(s)" in status_digest.bot_line()


def test_timetable_digest_reads_the_aggregate_contract_only(tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "timetable_automation": {
            "status": "failed",
            "last_accepted": {
                "run_id": "29944744744",
                "accepted_at": "2026-07-20T03:12:00+00:00",
            },
            "last_attempt": {
                "run_id": "30421182234",
                "outcome": "failure",
            },
            "next_action": "fresh delivery at the next due check",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    line = status_digest.timetable_line()

    assert "accepted run 29944744744 (2026-07-20)" in line
    assert "last run 30421182234: failure" in line
    assert "fresh delivery at the next due check" in line


def test_social_digest_reads_mode_and_durable_delivery_count(tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "social_deliveries": {
            "status": "enabled",
            "mode": "live",
            "deliveries": {"by_status": {"delivered": 3}},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.social_line() == (
        "*social*  enabled - live mode - 3 card(s) delivered")


def test_data_health_line_surfaces_operator_collapse(tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "data_health": {
            "status": "warning",
            "summary": {"operator_collapses": 1},
            "fleet": {"operator_collapses": [{
                "operator": "FBRI", "previous": 583, "current": 100,
            }]},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.data_health_line() == (
        "*data*  :warning: FBRI fleet count collapsed 583->100 - report-only")


def test_fleet_line_summarises_the_latest_guarded_refresh(
        tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "fleet_automation": {
            "status": "healthy",
            "last_attempt": {
                "outcome": "accepted",
                "finished_at": "2026-08-10T20:00:00+00:00",
                "live_records_before": 2605,
                "candidate_records": 2746,
                "added": 199,
                "removed": 58,
                "changed": 557,
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.fleet_line() == (
        "*fleet*  :white_check_mark: accepted (2026-08-10) - "
        "records 2605->2746 - +199/-58/557 changed"
    )


def test_blurb_line_surfaces_pending_review_and_bounded_usage(
        tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "blurb_generation": {
            "status": "pending_review",
            "pending_review": {"buses": 12, "lines": 36},
            "month_usage": {
                "requests": 3, "input_tokens": 1200, "output_tokens": 300,
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.blurb_line() == (
        "*blurbs*  12 bus(es) waiting for your review - "
        "3 request(s), 1500 token(s) this month")
