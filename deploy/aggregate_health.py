#!/usr/bin/env python3
"""Write the internal estate snapshot and notify only on incident transitions."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


STATE = Path("/var/lib/bristolbusbot/monitoring")
LIVE_DB = Path("/var/lib/bristolbusbot/collector/live.db")
AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
BOT_DB = Path("/var/lib/bristolbusbot/bot/app_data.db")
REMOTE_HOME = Path(os.environ.get("BBB_REMOTE_HOME", Path.home()))
PUBLISHED = REMOTE_HOME / "bus-audit-repo/docs/audit_data.json"
WEBHOOK = REMOTE_HOME / ".config/busbot-alerts/webhook"
SERVICES = ("bbb-site.service", "bbb-collector.service", "bbb-bot.service",
            "bbb-tunnel.service")
JOB_MAX_AGE_HOURS = {
    "backup": 27,
    "backup-check": 24 * 8,
    "audit-rollup": 30,
    "audit-publish": 30,
    "audit-snapshot": 30,
    "staleness": 2,
    "digest": 14,
}
TIMETABLE_DELIVERY_STATE = Path(
    "/var/lib/bristolbusbot/timetable-shadow/state.json")
TIMETABLE_PROMOTION_MARKER = Path(
    "/etc/bristolbusbot/timetable-promotion-enabled")
TIMETABLE_TOKEN_WARNING_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def age_seconds(value: str) -> float:
    seen = datetime.fromisoformat(value)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (utcnow() - seen.astimezone(timezone.utc)).total_seconds()


def service_checks() -> tuple[dict, list[str]]:
    checks, issues = {}, []
    for unit in SERVICES:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True,
            text=True, check=False)
        active = result.stdout.strip() == "active"
        checks[unit] = "active" if active else result.stdout.strip() or "unknown"
        if not active:
            issues.append(f"service:{unit}")
    return checks, issues


def sqlite_value(path: Path, query: str):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute(query).fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def job_checks() -> tuple[dict, list[str]]:
    checks, issues = {}, []
    for name, maximum_hours in JOB_MAX_AGE_HOURS.items():
        path = STATE / "jobs" / f"{name}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            success = payload.get("last_success_at")
            age_h = age_seconds(success) / 3600 if success else None
            healthy = (payload.get("last_result") != "failure" and
                       age_h is not None and age_h <= maximum_hours)
            checks[name] = {
                "result": payload.get("last_result"),
                "last_success_at": success,
                "age_hours": round(age_h, 2) if age_h is not None else None,
            }
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            healthy = False
            checks[name] = {"result": "missing", "error": str(exc)}
        if not healthy:
            issues.append(f"job:{name}")
    return checks, issues


def timetable_delivery_check() -> tuple[dict, list[str]]:
    enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-timetable-shadow.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    if not enabled:
        return {"status": "disabled"}, []
    issues: list[str] = []
    result: dict[str, object] = {"status": "enabled"}
    job_path = STATE / "jobs" / "timetable-shadow.json"
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        last_result = job.get("last_result")
        last_ok = (job.get("last_skipped_at")
                   if last_result == "skipped" else job.get("last_success_at"))
        age_h = age_seconds(last_ok) / 3600 if last_ok else None
        result["job"] = {
            "result": last_result,
            "last_ok_at": last_ok,
            "age_hours": round(age_h, 2) if age_h is not None else None,
        }
        if last_result == "failure" or age_h is None or age_h > 30:
            issues.append("job:timetable-shadow")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["job"] = {"result": "missing", "error": str(exc)}
        issues.append("job:timetable-shadow")

    try:
        state = json.loads(TIMETABLE_DELIVERY_STATE.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(
            str(state["token_expires_utc"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        days = (expires.astimezone(timezone.utc) - utcnow()).total_seconds() / 86400
        result["token"] = {
            "expires_utc": expires.astimezone(timezone.utc).isoformat(),
            "days_remaining": round(days, 1),
        }
        result["last_attempt"] = state.get("last_shadow_attempt")
        if days <= TIMETABLE_TOKEN_WARNING_DAYS:
            issues.append("credential:timetable-token-expiry")
    except (OSError, KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
        result["token"] = {"status": "missing", "error": str(exc)}
        issues.append("credential:timetable-token-expiry")
    return result, issues


def timetable_promotion_check() -> tuple[dict, list[str]]:
    marker = TIMETABLE_PROMOTION_MARKER
    if not marker.exists() and not marker.is_symlink():
        return {"status": "disabled"}, []
    issues: list[str] = []
    result: dict[str, object] = {"status": "enabled"}
    try:
        details = marker.lstat()
        safe = (not marker.is_symlink() and marker.is_file())
        if os.name != "nt":
            safe = (safe and details.st_uid == 0
                    and (details.st_mode & 0o777) == 0o644)
        if not safe:
            raise OSError("automatic-promotion marker is unsafe")
    except OSError as exc:
        result["marker"] = {"status": "unsafe", "error": str(exc)}
        return result, ["job:timetable-promote"]

    job_path = STATE / "jobs" / "timetable-promote.json"
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        last_result = job.get("last_result")
        last_ok = (job.get("last_skipped_at")
                   if last_result == "skipped" else job.get("last_success_at"))
        age_h = age_seconds(last_ok) / 3600 if last_ok else None
        result["job"] = {
            "result": last_result,
            "last_ok_at": last_ok,
            "age_hours": round(age_h, 2) if age_h is not None else None,
        }
        if last_result == "failure" or age_h is None or age_h > 30:
            issues.append("job:timetable-promote")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["job"] = {"result": "missing", "error": str(exc)}
        issues.append("job:timetable-promote")

    detail_path = STATE / "timetable-promotion.json"
    try:
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        outcome = detail.get("outcome")
        finished = detail.get("finished_at")
        age_h = age_seconds(finished) / 3600 if finished else None
        result["last_attempt"] = {
            "outcome": outcome,
            "finished_at": finished,
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "run_id": detail.get("run_id"),
            "failure_code": detail.get("failure_code"),
        }
        if outcome not in {"accepted", "no_change"} \
                or age_h is None or age_h > 24 * 8:
            issues.append("job:timetable-promote")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["last_attempt"] = {"outcome": "missing", "error": str(exc)}
        issues.append("job:timetable-promote")
    return result, issues


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except OSError:
        return False


def notify(text: str) -> None:
    try:
        url = WEBHOOK.read_text(encoding="utf-8").splitlines()[0].strip()
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
            return
        request = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=15).read()
    except OSError:
        pass


def main() -> int:
    issues: list[str] = []
    services, found = service_checks()
    issues.extend(found)
    jobs, found = job_checks()
    issues.extend(found)
    timetable_delivery, found = timetable_delivery_check()
    issues.extend(found)
    timetable_promotion, found = timetable_promotion_check()
    issues.extend(found)

    try:
        feed_at = sqlite_value(
            LIVE_DB,
            "SELECT last_success_at FROM poller_status WHERE name='siri_vm'")
        feed_age = age_seconds(feed_at) if feed_at else None
    except (OSError, sqlite3.Error, ValueError):
        feed_at, feed_age = None, None
    if feed_age is None or feed_age > 180:
        issues.append("feed:siri-vm")

    try:
        audit_day = sqlite_value(AUDIT_DB, "SELECT MAX(service_date) FROM daily_overall_summary")
    except (OSError, sqlite3.Error):
        audit_day = None
    try:
        publish_age_h = (utcnow().timestamp() - PUBLISHED.stat().st_mtime) / 3600
    except OSError:
        publish_age_h = None
    if publish_age_h is None or publish_age_h > 48:
        issues.append("publish:audit-data")

    site_ok = http_ok("http://127.0.0.1:5002/healthz")
    bot_ok = http_ok("http://127.0.0.1:3010/api/health")
    if not site_ok:
        issues.append("endpoint:site")
    if not bot_ok:
        issues.append("endpoint:bot")

    root_disk = shutil.disk_usage("/")
    root_free_pct = root_disk.free / root_disk.total * 100
    if root_free_pct < 15:
        issues.append("disk:root")
    backup_mounted = subprocess.run(
        ["findmnt", "-rn", "--mountpoint", "/mnt/bbb-backup"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not backup_mounted:
        issues.append("disk:backup-unmounted")

    try:
        last_post = sqlite_value(
            BOT_DB,
            "SELECT MAX(timestamp) FROM engagement_analytics WHERE post_uri IS NOT NULL")
    except (OSError, sqlite3.Error):
        last_post = None

    resource_file = STATE / "resource-samples.csv"
    resource_age = ((utcnow().timestamp() - resource_file.stat().st_mtime) / 60
                    if resource_file.exists() else None)
    if resource_age is None or resource_age > 15:
        issues.append("metrics:resource-samples")

    unique_issues = sorted(set(issues))
    snapshot = {
        "generated_at": utcnow().isoformat(),
        "status": "ok" if not unique_issues else "error",
        "issues": unique_issues,
        "services": services,
        "jobs": jobs,
        "timetable_delivery": timetable_delivery,
        "timetable_promotion": timetable_promotion,
        "feed": {"last_success_at": feed_at,
                 "age_seconds": round(feed_age, 1) if feed_age is not None else None},
        "audit": {"latest_rollup_service_date": audit_day,
                  "published_file_age_hours": round(publish_age_h, 2)
                  if publish_age_h is not None else None},
        "endpoints": {"site": site_ok, "bot": bot_ok},
        "disk": {"root_free_percent": round(root_free_pct, 1),
                 "backup_mounted": backup_mounted},
        "posting": {"last_success_at": last_post,
                    "silence_is_not_an_incident": True},
        "social_deliveries": {"status": "not_configured"},
        "resource_samples_age_minutes": round(resource_age, 1)
        if resource_age is not None else None,
    }
    atomic_json(STATE / "health.json", snapshot)

    incident_path = STATE / "incidents.json"
    try:
        previous = json.loads(incident_path.read_text(encoding="utf-8"))
        previous_issues = set(previous.get("active", []))
    except (OSError, json.JSONDecodeError):
        previous_issues = set()
    current = set(unique_issues)
    opened = sorted(current - previous_issues)
    resolved = sorted(previous_issues - current)
    if opened:
        notify(":rotating_light: BBB health incident: " + ", ".join(opened))
    if resolved:
        notify(":white_check_mark: BBB health recovery: " + ", ".join(resolved))
    atomic_json(incident_path, {
        "updated_at": utcnow().isoformat(), "active": unique_issues})
    print(json.dumps({"status": snapshot["status"], "issues": unique_issues}))
    return 1 if unique_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
