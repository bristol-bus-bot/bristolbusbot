import json
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from deploy import aggregate_health


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
    monkeypatch.setattr(aggregate_health, "http_ok", lambda _url: True)
    monkeypatch.setattr(aggregate_health, "notify", messages.append)
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


def test_new_timetable_success_notifies_slack_only_once(tmp_path, monkeypatch):
    state = tmp_path / "monitoring"
    state.mkdir()
    (state / "resource-samples.csv").write_text("sample\n", encoding="utf-8")
    published = tmp_path / "audit_data.json"
    published.write_text("{}\n", encoding="utf-8")
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
    monkeypatch.setattr(aggregate_health, "http_ok", lambda _url: True)
    monkeypatch.setattr(aggregate_health, "notify", messages.append)
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
    assert aggregate_health.main() == 0
    assert len(messages) == 1
