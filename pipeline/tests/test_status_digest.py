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
                "current_missing_fleet": 2,
                "missing_livery": 121,
                "current_missing_livery": 3,
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
        "*data*  :information_source: 65 identities unmatched across 56d "
        "(2 in the last 24h) - 121 source livery gaps (3 current) - "
        "blurb gaps 121/127/135 "
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
                "timetable_stop_transition_speeds": {"count": 0},
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
        "stop-transition speed flags 0 - near GPS gate 15 - match "
        "94.2%->94.7% - GPS p95 82m - report-only")


def plain_daily_snapshot():
    return {
        "status": "ok",
        "issues": [],
        "timetable_automation": {
            "last_accepted": {"run_id": "31070121269",
                              "accepted_at": "2026-08-10T04:20:00Z"},
            "delivery": {"job": {"last_ok_at": "2026-08-10T04:20:00Z"}},
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
            "backup": {"last_success_at": "2026-08-12T01:27:19Z",
                       "result": "success"},
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










def test_routine_skip_resets_the_stuck_count():
    snapshot = {"timetable_automation": {"status": "failed", "delivery": {"job": {
        "last_ok_at": "2026-10-16T04:00:00Z", "last_success_at": "2026-10-16T04:00:00Z",
        "last_skipped_at": "2026-10-21T04:00:00Z"}}}}
    assert status_digest.timetable_failing_days(snapshot, date(2026, 10, 22)) == 1


def test_moving_occasions_without_next_years_dates_are_flagged(tmp_path):
    from datetime import date
    import json as _json
    context = tmp_path / "editorial-context.json"
    context.write_text(_json.dumps({"occasions": [
        {"id": "easter-2027", "label": "Easter",
         "schedule": {"kind": "date_range", "start": "2027-03-28", "end": "2027-03-28"}},
        {"id": "diwali-2026", "label": "Diwali",
         "schedule": {"kind": "date_range", "start": "2026-11-06", "end": "2026-11-10"}},
        {"id": "diwali-2027", "label": "Diwali",
         "schedule": {"kind": "date_range", "start": "2027-10-29", "end": "2027-10-29"}},
        {"id": "halloween", "label": "Halloween",
         "schedule": {"kind": "annual_date", "month": 10, "day": 31}},
    ]}), encoding="utf-8")
    assert status_digest.moving_occasion_lines(date(2027, 4, 27), context) == []
    lines = status_digest.moving_occasion_lines(date(2027, 4, 28), context)
    assert lines == ["Occasions needing next year's dates: Easter. "
                     "Ask Claude to add them, checked against a source."]
    assert status_digest.moving_occasion_lines(date(2027, 4, 28), tmp_path / "missing.json") == []


def test_daily_phone_summary_only_says_the_state(monkeypatch):
    monkeypatch.setattr(status_digest, "recovery_status", lambda s, t=None: ([], []))
    assert status_digest.phone_summary({"status": "ok", "issues": []}) == (
        "Bus bot: all fine", "Everything is working.", 2)
    assert status_digest.phone_summary(
        {"status": "degraded", "issues": ["job:timetable-automation"]}) == (
        "Bus bot: a snag, nothing to do yet", "Details are in Slack.", 3)
    waiting = {"status": "ok", "issues": [],
               "blurb_generation": {"status": "pending_review", "pending_review": {"buses": 35}}}
    assert status_digest.phone_summary(waiting) == (
        "Bus bot: something needs your attention", "Details are in Slack.", 3)
    monkeypatch.setattr(status_digest, "recovery_status", lambda s, t=None: (
        ["Timetable updates have failed every check for 3 days."], []))
    assert status_digest.phone_summary({"status": "degraded", "issues": []})[2] == 4


def test_daily_phone_summary_only_goes_to_an_ntfy_topic(tmp_path, monkeypatch):
    sent = []

    class Response:
        def read(self):
            return b""

    monkeypatch.setattr(status_digest.urllib.request, "urlopen",
                        lambda request, timeout: sent.append(json.loads(request.data)) or Response())
    conf = tmp_path / "ntfy"
    monkeypatch.setattr(status_digest, "PHONE_CONF", conf)
    assert not status_digest._post_phone("t", "m", 2)
    conf.write_text("https://example.com/busbot-alerts-abcdefghijkl\n")
    assert not status_digest._post_phone("t", "m", 2)
    conf.write_text("https://ntfy.sh/busbot-alerts-abcdefghijkl\n")
    assert status_digest._post_phone("Bus bot: all fine", "m", 2)
    assert sent == [{"topic": "busbot-alerts-abcdefghijkl", "title": "Bus bot: all fine",
                     "message": "m", "priority": 2, "tags": ["bus"]}]


# --- #141: say what is happening now in plain words -------------------------

BACKUP_FAILURE = ("The backup has failed 2 times in a row, most recently on 17 Nov. "
                  "It ran out of time bundling the published audit site's history. "
                  "The last good backup is from 15 Nov.")


def quiet_snapshot():
    snapshot = plain_daily_snapshot()
    snapshot["timetable_automation"]["last_accepted"]["accepted_at"] = "2026-11-13T21:53:00Z"
    snapshot["collector_anomaly"] = {"status": "ok", "detectors": {"extreme_delays": {"count": 9}}}
    snapshot["failures"] = []
    return snapshot


def quiet_state(snapshot):
    return {"fingerprints": status_digest._progress_fingerprints(snapshot),
            "counts": status_digest.standing_counts(snapshot),
            "sent_at": "2026-11-14T08:00:00+00:00"}


def test_a_quiet_day_is_short_and_has_no_fixed_text(monkeypatch, tmp_path):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = quiet_snapshot()
    message = status_digest.daily_message(snapshot, quiet_state(snapshot),
                                          date(2026, 11, 15), tmp_path)
    assert "Where the plan stands" not in message
    assert "Everything is working." in message
    assert "*You need to do:* nothing." in message
    assert "Timetable: current, last updated 13 Nov." in message
    # Unchanged standing notes are left out.
    assert "missing source details" not in message
    assert "Odd readings" not in message
    assert "clues, not confirmed faults" not in message
    assert "Live bus information and the public website are current" not in message
    assert len(message.splitlines()) <= 12


def test_a_backup_failure_is_named_with_its_reason_and_never_blames_the_timetable(
        monkeypatch, tmp_path):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = quiet_snapshot()
    snapshot.update(status="error", issues=["job:backup"], failures=[BACKUP_FAILURE],
                    issue_areas={"job:backup": "the backup"})
    message = status_digest.daily_message(snapshot, quiet_state(snapshot),
                                          date(2026, 11, 17), tmp_path)
    assert BACKUP_FAILURE in message
    assert "Something needs looking at" in message
    assert "Ask Claude to look at the problem above" in message
    problem = message.split("*Since yesterday*")[0]
    assert "timetable" not in problem.lower()
    assert "Backup or timetable" not in message
    assert "nothing." not in message


def test_a_rejected_timetable_says_what_happened_and_that_the_old_one_stays(
        monkeypatch, tmp_path):
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = quiet_snapshot()
    snapshot.update(
        status="error", issues=["job:timetable-automation"],
        timetable_problem="The proposed timetable runs out of usable future service too soon.")
    snapshot["timetable_automation"].update(
        status="failed", delivery={"job": {"last_ok_at": "2026-11-13T05:01:00Z"}})
    message = status_digest.daily_message(snapshot, quiet_state(snapshot),
                                          date(2026, 11, 13), tmp_path)
    assert "A proposed timetable update was rejected." in message
    assert "runs out of usable future service too soon" in message
    assert "current timetable stays live" in message
    assert "*You need to do:* nothing." in message


def test_a_timetable_stuck_for_days_becomes_an_action(monkeypatch, tmp_path):
    """October 2026: 19 days of 'the Pi will retry' while every retry failed."""
    monkeypatch.setattr(status_digest, "_current_release_fingerprints", lambda: {})
    snapshot = quiet_snapshot()
    snapshot.update(status="error", issues=["job:timetable-automation"],
                    timetable_problem="Saturday has too little service.")
    snapshot["timetable_automation"].update(
        status="failed", last_accepted={"accepted_at": "2026-10-03T00:12:44Z"},
        delivery={"job": {"last_ok_at": "2026-10-03T00:12:44Z"}})
    message = status_digest.daily_message(snapshot, {}, date(2026, 10, 22), tmp_path)
    assert "Timetable updates have been rejected for 19 days running." in message
    assert message.count("Saturday has too little service.") == 1     # said once
    assert "Ask Claude to look at the problem above" in message


def test_an_old_or_unknown_timetable_is_a_problem():
    snapshot = quiet_snapshot()
    snapshot["timetable_automation"]["last_accepted"] = {"accepted_at": "2026-11-01T00:00:00Z"}
    assert any("last updated 16 days ago (1 Nov)" in line
               for line in status_digest.problems(snapshot, date(2026, 11, 17)))
    snapshot["timetable_automation"]["last_accepted"] = {}
    assert "The timetable's last update date is unknown." in status_digest.problems(
        snapshot, date(2026, 11, 17))


def test_standing_notes_appear_only_when_their_count_changes():
    snapshot = quiet_snapshot()
    state = quiet_state(snapshot)
    assert status_digest.change_lines(snapshot, state) == []
    snapshot["collector_anomaly"]["detectors"]["extreme_delays"]["count"] = 14
    assert status_digest.change_lines(snapshot, state) == [
        "Odd readings for Claude to look at (clues, not faults): 14, up from 9."]


def test_warnings_name_what_is_coming_up():
    line = status_digest.overall_line({
        "status": "warning",
        "issues": ["credential:timetable-token-renewal-due"],
        "issue_areas": {"credential:timetable-token-renewal-due":
                        "the GitHub timetable token (renew within 30 days)"},
    })
    assert line.startswith(":large_yellow_circle: *Everything is working.* Coming up soon:")
    assert "GitHub timetable token" in line


def test_health_notes_appear_under_today():
    note = ("The timetable correction for 2026-12-01 (planning #128) lapsed on "
            "2026-12-02 and is no longer used. It can go in a later tidy-up; "
            "nothing needs doing.")
    snapshot = quiet_snapshot()
    snapshot["notes"] = [note]
    message = status_digest.daily_message(snapshot, quiet_state(snapshot), date(2026, 12, 3),
                                          Path("does-not-exist"))
    assert f"- {note}" in message


def test_published_descriptions_are_one_line_and_only_when_new(tmp_path):
    (tmp_path / "a.approved.json").write_text(json.dumps({
        "created_at": "2026-11-16T22:30:00+00:00",
        "additions": {"in_service": {"FBRI-1": "x", "FBRI-2": "y"}, "waiting": {"FBRI-3": "z"}},
    }), encoding="utf-8")
    since = datetime.fromisoformat("2026-11-16T08:00:00+00:00")
    lines = status_digest.published_description_lines(date(2026, 11, 17), tmp_path, since)
    assert lines == ["3 new bus descriptions went live (3 this week). They show on each "
                     "bus's page on bristolbuses.live; ask Claude to pull any you don't like."]
    later = datetime.fromisoformat("2026-11-17T08:00:00+00:00")
    assert status_digest.published_description_lines(date(2026, 11, 18), tmp_path, later) == []
