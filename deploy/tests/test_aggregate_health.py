import json
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
