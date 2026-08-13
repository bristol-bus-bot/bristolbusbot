from datetime import date, datetime
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


def test_data_health_line_labels_source_gaps_without_implying_bad_refresh(
        tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "data_health": {
            "status": "warning",
            "summary": {
                "operator_collapses": 0,
                "missing_fleet": 65,
                "missing_livery": 121,
                "missing_blurbs": {
                    "in_service": 121,
                    "waiting": 127,
                    "depot": 135,
                },
                "missing_stop_localities": 0,
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.data_health_line() == (
        "*data*  :information_source: 65 sightings without a safe fleet match - "
        "121 source livery gaps - blurb gaps 121/127/135 "
        "(service/wait/depot) - 0 locality gaps - report-only")


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


def test_anomaly_line_surfaces_counts_and_match_drift(tmp_path, monkeypatch):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "collector_anomaly": {
            "status": "attention",
            "coverage": {"observations": 65799},
            "poll_metrics": {
                "older_half": {"match_rate": 0.9416},
                "recent_half": {"match_rate": 0.9471},
            },
            "detectors": {
                "extreme_delays": {"count": 1099},
                "backwards_stop_progress": {"count": 564},
                "impossible_implied_speeds": {"count": 0},
                "overlapping_vehicle_trips": {"count": 26},
                "gps_near_match_gate": {"count": 15},
                "gps_distance_m": {"p95": 82},
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(status_digest, "AGGREGATE_HEALTH", health)

    assert status_digest.anomaly_line() == (
        "*anomalies*  :warning: 48h/65,799 obs - extreme-delay readings "
        "1,099 - backwards flags 564 - trip-overlap flags 26 - "
        "impossible-speed flags 0 - near GPS gate 15 - match "
        "94.2%->94.7% - GPS p95 82m - report-only")


def plain_daily_snapshot():
    return {
        "status": "ok",
        "issues": [],
        "timetable_automation": {
            "last_accepted": {"run_id": "31070121269"},
        },
        "fleet_automation": {
            "last_attempt": {"finished_at": "2026-08-10T23:20:18Z"},
        },
        "locality_automation": {
            "last_attempt": {"finished_at": "2026-08-12T04:02:26Z"},
        },
        "blurb_generation": {
            "status": "healthy",
            "job": {"last_finished_at": "2026-08-11T22:21:52Z"},
        },
        "jobs": {
            "audit-publish": {"last_success_at": "2026-08-12T04:45:16Z"},
        },
        "data_health": {
            "summary": {
                "missing_fleet": 65,
                "missing_livery": 121,
                "missing_blurbs": {
                    "in_service": 121,
                    "waiting": 127,
                    "depot": 135,
                },
            },
        },
        "collector_anomaly": {"status": "attention"},
    }


def test_daily_message_explains_recent_work_and_plan_in_plain_english(monkeypatch):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})

    message = status_digest.daily_message(
        plain_daily_snapshot(), today=date(2026, 8, 13))

    assert "Everything important is working" in message
    assert "core move away from the Windows PC is complete" in message
    assert "next real job is to save better evidence" in message
    assert "Nothing today" in message
    assert "clues, not confirmed faults" in message
    for jargon in (
            "healthz", "matched-but-ungated", "mismatch canary", "report-only",
            "run_id", "token", "GB free", "aggregate"):
        assert jargon not in message


def test_daily_message_reports_only_changes_since_previous_summary(monkeypatch):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {
        "collector": "release-new",
    })
    snapshot = plain_daily_snapshot()
    current = status_digest._progress_fingerprints(snapshot)
    previous = dict(current)
    previous["audit_publish"] = "2026-08-11T04:45:16Z"
    previous["releases"] = {"collector": "release-old"}

    message = status_digest.daily_message(
        snapshot, {"fingerprints": previous}, date(2026, 8, 13))

    assert "Since yesterday" in message
    assert "public performance report published successfully" in message
    assert "software update was installed" in message
    assert "Finished over the last few days" not in message


def test_daily_message_makes_pending_description_review_the_only_action(
        monkeypatch):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = plain_daily_snapshot()
    snapshot["blurb_generation"] = {
        "status": "pending_review",
        "pending_review": {"buses": 12},
    }

    message = status_digest.daily_message(snapshot, today=date(2026, 8, 13))

    assert "Review descriptions for 12 buses when convenient" in message
    assert "Nothing will publish by itself" in message


def test_daily_message_says_safe_timetable_rejection_did_not_break_live_bot(
        monkeypatch):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = plain_daily_snapshot()
    snapshot.update({
        "status": "error",
        "issues": ["job:timetable-automation"],
        "timetable_automation": {
            "status": "failed",
            "last_accepted": {"run_id": "31070121269"},
            "last_attempt": {
                "outcome": "failure",
                "failure_code": "candidate_operator_collapse",
            },
        },
    })

    message = status_digest.daily_message(
        snapshot, today=date(2026, 8, 14))

    assert "live bot and website are working" in message
    assert "proposed timetable update looked incomplete" in message
    assert "current working timetable stayed live" in message
    assert "Pi will retry automatically" in message
    assert "needs attention" not in message
