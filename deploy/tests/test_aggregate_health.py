import json
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from deploy import aggregate_health


def test_data_health_findings_remain_report_only(tmp_path, monkeypatch):
    report = tmp_path / "data-health.json"
    report.write_text(json.dumps({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "report_only",
        "status": "warning",
        "summary": {"missing_livery": 2},
        "findings": [{"code": "observed_vehicle_missing_livery"}],
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "DATA_HEALTH_REPORT", report)

    result, issues = aggregate_health.data_health_check()

    assert result["status"] == "warning"
    assert result["mode"] == "report_only"
    assert issues == []


def test_fleet_automation_health_collapses_three_safe_steps_into_one_result(
        tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    marker = tmp_path / "fleet-refresh-enabled"
    marker.write_text("enabled=now\n", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    for name, result in (
        ("fleet-refresh", "success"),
        ("fleet-stage", "success"),
        ("enrichment-promote-fleet", "success"),
    ):
        (jobs / f"{name}.json").write_text(json.dumps({
            "last_result": result,
            "last_success_at": now,
            "last_finished_at": now,
        }), encoding="utf-8")
    promotion = state / "enrichment-fleet-promotion.json"
    promotion.write_text(json.dumps({
        "outcome": "accepted", "finished_at": now,
        "candidate": {"sha256": "b" * 64},
    }), encoding="utf-8")
    shadow = state / "fleet-shadow.json"
    shadow.write_text(json.dumps({
        "candidate": {"sha256": "b" * 64, "summary": {"records": 2746}},
        "live": {"sha256": "a" * 64, "summary": {"records": 2605}},
        "difference": {"added": 199, "removed": 58, "changed": 557},
        "operator_transitions": [{"legacy": "VITR", "replacement": "KEMT"}],
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "FLEET_REFRESH_MARKER", marker)
    monkeypatch.setattr(aggregate_health, "FLEET_PROMOTION_STATE", promotion)
    monkeypatch.setattr(aggregate_health, "FLEET_SHADOW_REPORT", shadow)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result, issues = aggregate_health.fleet_automation_check()

    assert issues == []
    assert result["status"] == "healthy"
    assert result["last_attempt"]["candidate_records"] == 2746
    assert result["last_attempt"]["added"] == 199


def test_fleet_automation_failure_says_existing_data_is_safe(
        tmp_path, monkeypatch):
    refresh = {
        "status": "failed",
        "failure_code": "refresh_or_promotion_failed",
        "last_attempt": {"outcome": "failed_before_replace"},
    }

    message = aggregate_health.fleet_failure_message(refresh)

    assert "needs attention" in message
    assert "existing fleet data remains live" in message
    assert "do not need to check routine successful runs" in message


def test_fleet_refresh_failure_is_not_hidden_by_missing_downstream_jobs(
        tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    marker = tmp_path / "fleet-refresh-enabled"
    marker.write_text("enabled=now\n", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    (jobs / "fleet-refresh.json").write_text(json.dumps({
        "last_result": "failure",
        "last_finished_at": now,
        "failure_code": "command_failed",
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "FLEET_REFRESH_MARKER", marker)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result, issues = aggregate_health.fleet_automation_check()

    assert result["status"] == "failed"
    assert issues == ["job:fleet-automation"]


def test_locality_automation_correlates_exact_candidate_and_coverage(
        tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    for name, result in (
        ("locality-refresh", "success"),
        ("locality-stage", "success"),
        ("enrichment-promote-localities", "success"),
    ):
        (jobs / f"{name}.json").write_text(json.dumps({
            "last_result": result,
            "last_success_at": now,
            "last_finished_at": now,
        }), encoding="utf-8")
    promotion = state / "enrichment-localities-promotion.json"
    promotion.write_text(json.dumps({
        "outcome": "accepted",
        "finished_at": now,
        "candidate": {"sha256": "a" * 64},
    }), encoding="utf-8")
    shadow = state / "locality-shadow.json"
    shadow.write_text(json.dumps({
        "candidate": {
            "sha256": "a" * 64,
            "summary": {"records": 4815},
        },
        "coverage": {"missing": 0, "extra": 0},
        "boundary": {"edition": "December 2025"},
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(
        aggregate_health, "LOCALITY_PROMOTION_STATE", promotion)
    monkeypatch.setattr(aggregate_health, "LOCALITY_SHADOW_REPORT", shadow)
    marker = tmp_path / "locality-refresh-enabled"
    marker.write_text("enabled=now\n", encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "LOCALITY_REFRESH_MARKER", marker)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result, issues = aggregate_health.locality_automation_check()

    assert result["status"] == "healthy"
    assert result["last_attempt"]["records"] == 4815
    assert issues == []


def test_social_curation_health_reads_enabled_job_and_ledger(
        tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (jobs / "social-curation.json").write_text(json.dumps({
        "last_result": "success",
        "last_success_at": now,
    }), encoding="utf-8")
    database = tmp_path / "social.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE deliveries (status TEXT, updated_at TEXT)")
    connection.executemany(
        "INSERT INTO deliveries VALUES (?, ?)",
        (("delivered", now), ("rendered", now)),
    )
    connection.commit()
    connection.close()
    config = tmp_path / "social.env"
    token = tmp_path / "social-slack.token"
    marker = tmp_path / "social-live-enabled"
    for path in (config, token, marker):
        path.write_text("present\n", encoding="utf-8")

    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "SOCIAL_DB", database)
    monkeypatch.setattr(aggregate_health, "SOCIAL_CONFIG", config)
    monkeypatch.setattr(aggregate_health, "SOCIAL_TOKEN", token)
    monkeypatch.setattr(aggregate_health, "SOCIAL_LIVE_MARKER", marker)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    result, issues = aggregate_health.social_curation_check()

    assert issues == []
    assert result["status"] == "enabled"
    assert result["mode"] == "live"
    assert result["deliveries"]["by_status"] == {
        "delivered": 1, "rendered": 1}


def test_incident_notifies_once_and_recovery_notifies_once(tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    state.mkdir()
    (state / "resource-samples.csv").write_text("sample\n", encoding="utf-8")
    published = tmp_path / "audit_data.json"
    published.write_text("{}\n", encoding="utf-8")
    messages = []
    unhealthy = {"value": True}

    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "PUBLISHED", published)
    monkeypatch.setattr(
        aggregate_health, "service_checks",
        lambda: ({"bbb-site.service": "failed"}, ["service:bbb-site.service"])
        if unhealthy["value"] else ({"bbb-site.service": "active"}, []))
    monkeypatch.setattr(aggregate_health, "job_checks", lambda: ({}, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check", lambda: ({"status": "disabled"}, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check", lambda: ({"status": "disabled"}, []))
    monkeypatch.setattr(
        aggregate_health, "editorial_refresh_check", lambda: ({"status": "disabled"}, []))
    monkeypatch.setattr(
        aggregate_health, "social_curation_check",
        lambda: ({"status": "not_configured", "mode": "shadow"}, []))
    monkeypatch.setattr(aggregate_health, "http_ok", lambda _url: True)
    def notify(message):
        messages.append(message)
        return True

    monkeypatch.setattr(aggregate_health, "notify", notify)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        aggregate_health.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=10, free=90))

    now = datetime.now(timezone.utc).isoformat()

    def sqlite_value(_path, query):
        if "poller_status" in query:
            return now
        if "service_date" in query:
            return "20260716"
        return now

    monkeypatch.setattr(aggregate_health, "sqlite_value", sqlite_value)

    assert aggregate_health.main() == 1
    assert len(messages) == 1
    assert "incident" in messages[0]
    assert aggregate_health.main() == 1
    assert len(messages) == 1

    unhealthy["value"] = False
    assert aggregate_health.main() == 0
    assert len(messages) == 2
    assert "recovery" in messages[1]
    assert aggregate_health.main() == 0
    assert len(messages) == 2


def test_timetable_delivery_health_accepts_recent_skip_and_warns_on_token(tmp_path, monkeypatch):
    monitoring = tmp_path / "monitoring"
    jobs = monitoring / "jobs"
    jobs.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (jobs / "timetable-shadow.json").write_text(json.dumps({
        "last_result": "skipped",
        "last_skipped_at": now.isoformat(),
    }), encoding="utf-8")
    delivery_state = tmp_path / "delivery-state.json"
    delivery_state.write_text(json.dumps({
        "token_expires_utc": (now + timedelta(days=10)).isoformat(),
        "last_shadow_attempt": {"outcome": "success"},
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "STATE", monitoring)
    monkeypatch.setattr(aggregate_health, "TIMETABLE_DELIVERY_STATE", delivery_state)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    check, issues = aggregate_health.timetable_delivery_check()
    assert check["job"]["result"] == "skipped"
    assert "job:timetable-shadow" not in issues
    assert "credential:timetable-token-expiry" in issues


def test_timetable_promotion_health_keeps_rejection_visible(tmp_path, monkeypatch):
    monitoring = tmp_path / "monitoring"
    jobs = monitoring / "jobs"
    jobs.mkdir(parents=True)
    marker = SimpleNamespace(
        exists=lambda: True,
        is_symlink=lambda: False,
        is_file=lambda: True,
        lstat=lambda: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644),
    )
    now = datetime.now(timezone.utc)
    (jobs / "timetable-promote.json").write_text(json.dumps({
        "last_result": "success",
        "last_success_at": now.isoformat(),
    }), encoding="utf-8")
    detail = monitoring / "timetable-promotion.json"
    detail.write_text(json.dumps({
        "outcome": "accepted",
        "finished_at": now.isoformat(),
        "run_id": "123",
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "STATE", monitoring)
    monkeypatch.setattr(aggregate_health, "TIMETABLE_PROMOTION_MARKER", marker)

    check, issues = aggregate_health.timetable_promotion_check()
    assert check["last_attempt"]["outcome"] == "accepted"
    assert issues == []

    detail.write_text(json.dumps({
        "outcome": "rolled_back",
        "finished_at": now.isoformat(),
        "failure_code": "consumer_unhealthy",
    }), encoding="utf-8")
    check, issues = aggregate_health.timetable_promotion_check()
    assert check["last_attempt"]["outcome"] == "rolled_back"
    assert issues == ["job:timetable-promote"]


def test_timetable_messages_explain_success_and_safe_rollback():
    success = aggregate_health.timetable_success_message({
        "outcome": "accepted",
        "finished_at": "2026-07-29T04:12:00+00:00",
        "run_id": "29913612013",
        "database_sha256": "a" * 64,
        "duration_seconds": 126.7,
        "tnds_status": "not_needed",
        "validation": {
            "latest_service": "20270530",
            "routes": 251,
            "trips": 54466,
            "stops": 6437,
            "stop_times": 1964503,
            "route_shapes": 413,
            "stop_routes": 12000,
            "superseded_route_editions": 146,
        },
    })
    assert "54,466 trips" in success
    assert "1,964,503 stop times" in success
    assert "stop search" in success
    assert "146 overlapping route editions" in success
    assert "run 29913612013" in success

    failure = aggregate_health.timetable_failure_message("promotion", {
        "outcome": "rolled_back",
        "finished_at": "2026-07-29T04:12:00+00:00",
        "run_id": "29913612013",
        "failure_code": "consumer_unhealthy",
        "error": "site rejected the promoted timetable",
        "recovery_healthy": True,
    })
    assert "rolled back" in failure
    assert "previous timetable was restored" in failure
    assert "blocked from replay" in failure
    assert "site rejected" not in failure


def test_new_timetable_success_notifies_slack_only_once(tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    state.mkdir()
    (state / "resource-samples.csv").write_text("sample\n", encoding="utf-8")
    published = tmp_path / "audit_data.json"
    published.write_text("{}\n", encoding="utf-8")
    (state / "incidents.json").write_text(json.dumps({
        "active": ["job:timetable-automation"],
        "timetable_recovery_pending": True,
    }), encoding="utf-8")
    messages = []
    now = datetime.now(timezone.utc).isoformat()
    attempt = {
        "outcome": "accepted",
        "finished_at": now,
        "run_id": "123",
        "validation": {},
    }

    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "PUBLISHED", published)
    monkeypatch.setattr(
        aggregate_health, "service_checks", lambda: ({}, []))
    monkeypatch.setattr(aggregate_health, "job_checks", lambda: ({}, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check",
        lambda: ({"status": "enabled", "last_attempt": {}}, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check",
        lambda: ({"status": "enabled", "last_attempt": attempt}, []))
    monkeypatch.setattr(
        aggregate_health, "editorial_refresh_check",
        lambda: ({"status": "disabled"}, []))
    monkeypatch.setattr(
        aggregate_health, "social_curation_check",
        lambda: ({"status": "not_configured", "mode": "shadow"}, []))
    monkeypatch.setattr(aggregate_health, "http_ok", lambda _url: True)

    def notify(message):
        messages.append(message)
        return True

    monkeypatch.setattr(aggregate_health, "notify", notify)
    monkeypatch.setattr(
        aggregate_health, "timetable_automation_check",
        lambda: ({
            "status": "healthy",
            "phase": "complete",
            "delivery": {"status": "enabled"},
            "promotion": {"status": "enabled"},
            "last_attempt": attempt,
            "last_accepted": {
                "run_id": "123",
                "accepted_at": now,
            },
        }, []))
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        aggregate_health.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=10, free=90))

    def sqlite_value(_path, query):
        if "poller_status" in query:
            return now
        if "service_date" in query:
            return "20260716"
        return now

    monkeypatch.setattr(aggregate_health, "sqlite_value", sqlite_value)

    assert aggregate_health.main() == 0
    assert len(messages) == 1
    assert "Timetable updated automatically" in messages[0]
    incident = json.loads(
        (state / "incidents.json").read_text(encoding="utf-8"))
    assert incident["timetable_recovery_pending"] is False
    assert aggregate_health.main() == 0
    assert len(messages) == 1


def test_failed_shadow_never_inherits_old_promotion_success(monkeypatch):
    shadow = {
        "status": "enabled",
        "job": {"result": "failure", "failure_code": "lock_timeout",
                "age_hours": 0.1,
                "last_failure_at": "2026-07-29T05:00:00+00:00"},
        "last_attempt": {
            "outcome": "success",
            "run_id": "29944744744",
            "database_sha256": "a" * 64,
        },
    }
    old_promotion = {
        "status": "enabled",
        "job": {"result": "success", "age_hours": 400},
        "last_attempt": {
            "outcome": "accepted",
            "run_id": "29944744744",
            "database_sha256": "a" * 64,
        },
        "last_accepted": {"run_id": "29944744744"},
    }
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check", lambda: (shadow, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check",
        lambda: (old_promotion, []))

    check, issues = aggregate_health.timetable_automation_check()

    assert check["status"] == "failed"
    assert check["phase"] == "shadow"
    assert check["promotion_expected"] is False
    assert check["last_attempt"]["run_id"] == "29944744744"
    assert check["last_attempt"]["failure_code"] == "lock_timeout"
    assert check["last_attempt"]["outcome"] == "failure"
    assert "promotion not attempted" in check["summary"]
    assert issues == ["job:timetable-automation"]


def test_mismatched_wrapper_and_detail_report_the_new_failure(monkeypatch):
    shadow = {
        "status": "enabled",
        "job": {"result": "success", "age_hours": 2},
        "last_attempt": {
            "outcome": "success",
            "run_id": "30421182234",
            "database_sha256": "b" * 64,
            "finished_at": "2026-07-29T05:00:00+00:00",
        },
    }
    promotion = {
        "status": "enabled",
        "job": {"result": "failure", "failure_code": "lock_timeout",
                "last_ok_at": "2026-07-29T05:01:00+00:00",
                "last_failure_at": "2026-07-29T05:01:00+00:00"},
        "last_attempt": {
            "outcome": "accepted",
            "run_id": "29944744744",
            "database_sha256": "a" * 64,
        },
    }
    monkeypatch.setattr(aggregate_health, "age_seconds", lambda _value: 7200)
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check", lambda: (shadow, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check", lambda: (promotion, []))

    check, issues = aggregate_health.timetable_automation_check()

    assert check["status"] == "failed"
    assert check["last_attempt"]["run_id"] == "30421182234"
    assert check["last_attempt"]["failure_code"] == "lock_timeout"
    assert check["last_attempt"]["outcome"] == "failure"
    assert issues == ["job:timetable-automation"]


def test_unsafe_promotion_marker_is_visible_even_during_delivery_cooldown(
        monkeypatch):
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check",
        lambda: ({
            "status": "enabled",
            "job": {"result": "skipped", "age_hours": 0.1},
        }, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check",
        lambda: ({
            "status": "enabled",
            "marker": {"status": "unsafe"},
        }, ["job:timetable-promote"]))

    check, issues = aggregate_health.timetable_automation_check()

    assert check["status"] == "failed"
    assert check["last_attempt"]["failure_code"] == "unsafe_promotion_marker"
    assert issues == ["job:timetable-automation"]


def test_attended_shadow_is_diagnostic_and_does_not_expect_promotion(monkeypatch):
    shadow = {
        "status": "enabled",
        "job": {"result": "success", "age_hours": 0.1},
        "last_attempt": {
            "outcome": "success",
            "mode": "attended",
            "run_id": "30421182234",
            "database_sha256": "b" * 64,
            "finished_at": "2026-07-29T05:00:00+00:00",
        },
    }
    stale_promotion = {
        "status": "enabled",
        "job": {
            "result": "failure",
            "failure_code": "command_failed",
            "last_failure_at": "2026-07-28T05:00:00+00:00",
        },
        "last_attempt": {
            "outcome": "rolled_back",
            "run_id": "29944744744",
            "database_sha256": "a" * 64,
        },
    }
    monkeypatch.setattr(
        aggregate_health, "timetable_delivery_check", lambda: (shadow, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_promotion_check",
        lambda: (stale_promotion, ["job:timetable-promote"]))

    check, issues = aggregate_health.timetable_automation_check()

    assert check["status"] == "idle"
    assert check["phase"] == "attended_shadow"
    assert check["promotion_expected"] is False
    assert "production is unchanged" in check["summary"]
    assert issues == []


def test_blurb_generation_reports_pending_review_without_an_incident(
        tmp_path, monkeypatch):
    marker = tmp_path / "enabled"
    marker.write_text("enabled=now\n", encoding="utf-8")
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({
        "status": "pending_review",
        "batch_id": "20260811T120000Z-abcdef12",
        "created_at": "2026-08-11T12:00:00+00:00",
        "additions": {
            "in_service": {"OPAA:101": "one"},
            "waiting": {"OPAA:101": "two"},
            "depot": {"OPAA:101": "three"},
        },
    }), encoding="utf-8")
    usage = tmp_path / "usage.json"
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    usage.write_text(json.dumps({
        "schema": 1,
        "events": [{
            "month": month,
            "actual_input_tokens": 100,
            "actual_output_tokens": 20,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(aggregate_health, "BLURB_GENERATION_MARKER", marker)
    monkeypatch.setattr(aggregate_health, "BLURB_PENDING", pending)
    monkeypatch.setattr(aggregate_health, "BLURB_USAGE", usage)
    monkeypatch.setattr(
        aggregate_health, "_fleet_job",
        lambda name: {"result": "success", "age_hours": 1, "name": name})
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    status, issues = aggregate_health.blurb_generation_check()

    assert status["status"] == "pending_review"
    assert status["pending_review"] == {
        "batch_id": "20260811T120000Z-abcdef12",
        "buses": 1,
        "lines": 3,
        "created_at": "2026-08-11T12:00:00+00:00",
    }
    assert status["month_usage"] == {
        "requests": 1, "input_tokens": 100, "output_tokens": 20}
    assert issues == []


def test_timetable_notification_state_advances_only_after_successful_send(
        tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    state.mkdir()
    (state / "resource-samples.csv").write_text("sample\n", encoding="utf-8")
    published = tmp_path / "audit_data.json"
    published.write_text("{}\n", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    attempt = {
        "outcome": "failure",
        "finished_at": now,
        "run_id": "30421182234",
        "database_sha256": "b" * 64,
        "failure_code": "lock_timeout",
        "context": {"phase": "promotion"},
    }
    automation = {
        "status": "failed",
        "phase": "promotion",
        "delivery": {"status": "enabled"},
        "promotion": {"status": "enabled"},
        "last_attempt": attempt,
    }
    sends = iter((False, True))
    messages = []

    monkeypatch.setattr(aggregate_health, "STATE", state)
    monkeypatch.setattr(aggregate_health, "PUBLISHED", published)
    monkeypatch.setattr(aggregate_health, "service_checks", lambda: ({}, []))
    monkeypatch.setattr(aggregate_health, "job_checks", lambda: ({}, []))
    monkeypatch.setattr(
        aggregate_health, "timetable_automation_check", lambda: (automation, [
            "job:timetable-automation"]))
    monkeypatch.setattr(
        aggregate_health, "editorial_refresh_check",
        lambda: ({"status": "disabled"}, []))
    monkeypatch.setattr(
        aggregate_health, "social_curation_check",
        lambda: ({"status": "not_configured", "mode": "shadow"}, []))
    monkeypatch.setattr(aggregate_health, "http_ok", lambda _url: True)

    def notify(message):
        messages.append(message)
        return next(sends)

    monkeypatch.setattr(aggregate_health, "notify", notify)
    monkeypatch.setattr(
        aggregate_health.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        aggregate_health.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=10, free=90))
    monkeypatch.setattr(
        aggregate_health, "sqlite_value",
        lambda _path, query: (now if "poller_status" in query else "20260716"))

    assert aggregate_health.main() == 1
    first = json.loads((state / "incidents.json").read_text(encoding="utf-8"))
    assert first["notified_timetable_failure_fingerprints"] == []
    assert aggregate_health.main() == 1
    second = json.loads((state / "incidents.json").read_text(encoding="utf-8"))
    assert len(second["notified_timetable_failure_fingerprints"]) == 1
    assert aggregate_health.main() == 1
    assert len(messages) == 2
