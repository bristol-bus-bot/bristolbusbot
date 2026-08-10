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
from zoneinfo import ZoneInfo


STATE = Path("/var/lib/bristolbusbot/monitoring")
LIVE_DB = Path("/var/lib/bristolbusbot/collector/live.db")
AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
BOT_DB = Path("/var/lib/bristolbusbot/bot/app_data.db")
SOCIAL_DB = Path("/var/lib/bristolbusbot/social/social.db")
SOCIAL_CONFIG = Path("/etc/bristolbusbot/social.env")
SOCIAL_TOKEN = Path("/etc/bristolbusbot/social-slack.token")
SOCIAL_LIVE_MARKER = Path("/etc/bristolbusbot/social-live-enabled")
DATA_HEALTH_REPORT = Path(
    "/var/lib/bristolbusbot/monitoring/data-health.json")
FLEET_REFRESH_MARKER = Path("/etc/bristolbusbot/fleet-refresh-enabled")
FLEET_PROMOTION_STATE = Path(
    "/var/lib/bristolbusbot/monitoring/enrichment-fleet-promotion.json")
FLEET_SHADOW_REPORT = Path(
    "/var/lib/bristolbusbot/monitoring/fleet-shadow.json")
FLEET_MAX_AGE_HOURS = 24 * 8
LOCALITY_PROMOTION_STATE = Path(
    "/var/lib/bristolbusbot/monitoring/enrichment-localities-promotion.json")
LOCALITY_SHADOW_REPORT = Path(
    "/var/lib/bristolbusbot/monitoring/locality-shadow.json")
LOCALITY_REFRESH_MARKER = Path(
    "/etc/bristolbusbot/locality-refresh-enabled")
LOCALITY_MAX_AGE_HOURS = 30
BLURB_GENERATION_MARKER = Path(
    "/etc/bristolbusbot/blurb-generation-enabled")
BLURB_PENDING = Path("/var/lib/bristolbusbot/blurb-pending/pending.json")
BLURB_USAGE = Path("/var/lib/bristolbusbot/monitoring/blurb-usage.json")
BLURB_MAX_AGE_HOURS = 24 * 8
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
    "data-health": 30,
}
TIMETABLE_DELIVERY_STATE = Path(
    "/var/lib/bristolbusbot/timetable-shadow/state.json")
TIMETABLE_PROMOTION_MARKER = Path(
    "/etc/bristolbusbot/timetable-promotion-enabled")
TIMETABLE_TOKEN_WARNING_DAYS = 30
BRISTOL_TZ = ZoneInfo("Europe/London")
TIMETABLE_RUN_URL = (
    "https://github.com/bristol-bus-bot/bristolbusbot/actions/runs/{}")
EDITORIAL_STATE = Path("/var/lib/bristolbusbot-editorial/state.json")
EDITORIAL_FILE_URL = (
    "https://github.com/bristol-bus-bot/bristolbusbot/blob/main/"
    "bot/data/editorial-context.json")


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
            "failure_code": job.get("failure_code"),
            "last_finished_at": job.get("last_finished_at"),
            "last_failure_at": job.get("last_failure_at"),
        }
        if last_result == "failure" or age_h is None or age_h > 30:
            issues.append("job:timetable-shadow")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["job"] = {"result": "missing", "error": str(exc)}
        issues.append("job:timetable-shadow")

    try:
        state = json.loads(TIMETABLE_DELIVERY_STATE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("delivery state is not an object")
        result["last_attempt"] = (
            state.get("last_attempt") or state.get("last_shadow_attempt"))
        result["last_success"] = state.get("last_success")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        state = {}
        result["last_attempt"] = {"outcome": "missing", "error": str(exc)}

    try:
        expires = datetime.fromisoformat(
            str(state["token_expires_utc"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        days = (expires.astimezone(timezone.utc) - utcnow()).total_seconds() / 86400
        result["token"] = {
            "expires_utc": expires.astimezone(timezone.utc).isoformat(),
            "days_remaining": round(days, 1),
        }
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
            "failure_code": job.get("failure_code"),
            "last_finished_at": job.get("last_finished_at"),
            "last_failure_at": job.get("last_failure_at"),
        }
        if last_result == "failure":
            issues.append("job:timetable-promote")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["job"] = {"result": "missing", "error": str(exc)}
        issues.append("job:timetable-promote")

    detail_path = STATE / "timetable-promotion.json"
    try:
        document = json.loads(detail_path.read_text(encoding="utf-8"))
        detail = document.get("last_attempt")
        if not isinstance(detail, dict):
            detail = document
        outcome = detail.get("outcome")
        finished = detail.get("finished_at")
        age_h = age_seconds(finished) / 3600 if finished else None
        result["last_attempt"] = {
            "outcome": outcome,
            "mode": detail.get("mode"),
            "finished_at": finished,
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "run_id": detail.get("run_id"),
            "commit": detail.get("commit"),
            "database_sha256": detail.get("database_sha256"),
            "previous_sha256": detail.get("previous_sha256"),
            "duration_seconds": detail.get("duration_seconds"),
            "validation": detail.get("validation"),
            "tnds_status": detail.get("tnds_status"),
            "failure_code": detail.get("failure_code"),
            "error": detail.get("error"),
            "recovery_healthy": detail.get("recovery_healthy"),
            "context": detail.get("context"),
            "attempt_id": detail.get("attempt_id"),
        }
        accepted = document.get("last_accepted")
        if isinstance(accepted, dict):
            result["last_accepted"] = accepted
        elif document.get("last_accepted_run_id"):
            result["last_accepted"] = {
                "run_id": document.get("last_accepted_run_id"),
                "accepted_at": document.get("last_accepted_at"),
                "database_sha256": document.get("database_sha256"),
                "commit": document.get("commit"),
            }
        if outcome not in {"accepted", "no_change"}:
            issues.append("job:timetable-promote")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["last_attempt"] = {"outcome": "missing", "error": str(exc)}
        issues.append("job:timetable-promote")
    return result, issues


def _attempt_identity(attempt: object) -> tuple[str, str]:
    if not isinstance(attempt, dict):
        return "", ""
    return (
        str(attempt.get("run_id") or ""),
        str(attempt.get("database_sha256") or ""),
    )


def _not_older(left: object, right: object) -> bool:
    """Return whether left is at/after right; missing timestamps fail closed."""
    if not left or not right:
        return True
    try:
        left_time = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        right_time = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        if left_time.tzinfo is None:
            left_time = left_time.replace(tzinfo=timezone.utc)
        if right_time.tzinfo is None:
            right_time = right_time.replace(tzinfo=timezone.utc)
        return left_time.astimezone(timezone.utc) >= right_time.astimezone(timezone.utc)
    except ValueError:
        return True


def timetable_automation_check() -> tuple[dict, list[str]]:
    """Correlate shadow and promotion as one causal transaction."""
    delivery, delivery_issues = timetable_delivery_check()
    promotion, _ = timetable_promotion_check()
    result: dict[str, object] = {
        "status": "idle",
        "delivery": delivery,
        "promotion": promotion,
        "last_accepted": promotion.get("last_accepted"),
    }
    if delivery.get("status") == "disabled":
        result["status"] = "disabled"
        result["next_action"] = "timer disabled"
        return result, []

    issues: list[str] = [
        issue for issue in delivery_issues
        if issue.startswith("credential:")
    ]
    delivery_job = delivery.get("job")
    delivery_job = delivery_job if isinstance(delivery_job, dict) else {}
    delivery_attempt = delivery.get("last_attempt")
    delivery_attempt = delivery_attempt if isinstance(delivery_attempt, dict) else {}
    promotion_job = promotion.get("job")
    promotion_job = promotion_job if isinstance(promotion_job, dict) else {}
    promotion_attempt = promotion.get("last_attempt")
    promotion_attempt = promotion_attempt if isinstance(promotion_attempt, dict) else {}
    result["last_attempt"] = delivery_attempt

    marker = promotion.get("marker")
    if isinstance(marker, dict) and marker.get("status") == "unsafe":
        result.update({
            "status": "failed",
            "phase": "promotion",
            "last_attempt": {
                "outcome": "failure",
                "finished_at": utcnow().isoformat(),
                "failure_code": "unsafe_promotion_marker",
                "context": {"phase": "promotion"},
            },
            "promotion_expected": False,
            "summary": "automatic promotion marker is unsafe",
            "next_action": "repair or remove the promotion marker",
        })
        issues.append("job:timetable-automation")
        return result, sorted(set(issues))

    token = delivery.get("token")
    if isinstance(token, dict):
        days = token.get("days_remaining")
        if isinstance(days, (int, float)) and days <= TIMETABLE_TOKEN_WARNING_DAYS:
            issues.append("credential:timetable-token-expiry")

    if delivery_job.get("result") == "failure" \
            or delivery_attempt.get("outcome") == "failure":
        recorded_failure = delivery_attempt.get("outcome") == "failure" \
            and delivery_job.get("failure_code") != "lock_timeout"
        failure_attempt = delivery_attempt if recorded_failure else {
            "outcome": "failure",
            "run_id": delivery_attempt.get("run_id"),
            "database_sha256": delivery_attempt.get("database_sha256"),
            "finished_at": delivery_job.get("last_failure_at")
            or delivery_job.get("last_finished_at"),
            "failure_code": delivery_job.get("failure_code")
            or delivery_attempt.get("failure_code")
            or "shadow_wrapper_failed",
            "context": {"phase": "shadow"},
        }
        result.update({
            "status": "failed",
            "phase": "shadow",
            "last_attempt": failure_attempt,
            "promotion_expected": False,
            "summary": "promotion not attempted; existing timetable retained",
            "next_action": "fresh delivery at the next due check",
        })
        issues.append("job:timetable-automation")
        return result, sorted(set(issues))

    job_age = delivery_job.get("age_hours")
    if job_age is None or (isinstance(job_age, (int, float)) and job_age > 30):
        result.update({
            "status": "overdue",
            "phase": "shadow",
            "promotion_expected": False,
            "summary": "timetable delivery check is overdue",
            "next_action": "inspect the shadow timer",
        })
        issues.append("job:timetable-automation")
        return result, sorted(set(issues))

    if delivery_job.get("result") == "skipped":
        result.update({
            "status": "idle",
            "phase": "cooldown",
            "promotion_expected": False,
            "summary": "recent successful delivery; no new promotion required",
            "next_action": "next scheduled due check",
        })
        return result, sorted(set(issues))

    if delivery_attempt.get("outcome") != "success":
        result.update({
            "status": "idle",
            "phase": "waiting",
            "promotion_expected": False,
            "summary": "no completed timetable candidate is awaiting promotion",
            "next_action": "next scheduled due check",
        })
        return result, sorted(set(issues))

    delivery_identity = _attempt_identity(delivery_attempt)
    promotion_identity = _attempt_identity(promotion_attempt)
    promotion_failed_after_delivery = (
        promotion_job.get("result") == "failure"
        and _not_older(
            promotion_job.get("last_failure_at")
            or promotion_job.get("last_finished_at"),
            delivery_attempt.get("finished_at"),
        )
    )
    if promotion_failed_after_delivery:
        detailed_failure = (
            delivery_identity == promotion_identity
            and promotion_attempt.get("outcome") in {
                "rejected", "rolled_back", "rollback_failed"}
        )
        failure_attempt = promotion_attempt if detailed_failure else {
            "outcome": "failure",
            "run_id": delivery_attempt.get("run_id"),
            "database_sha256": delivery_attempt.get("database_sha256"),
            "finished_at": promotion_job.get("last_failure_at")
            or promotion_job.get("last_finished_at"),
            "failure_code": promotion_job.get("failure_code")
            or "promotion_wrapper_failed",
            "context": {"phase": "promotion"},
        }
        result.update({
            "status": "failed",
            "phase": "promotion",
            "promotion_expected": False,
            "last_attempt": failure_attempt,
            "summary": "the correlated promotion job failed",
            "next_action": "inspect the correlated promotion job",
        })
        issues.append("job:timetable-automation")
        return result, sorted(set(issues))
    if delivery_identity != promotion_identity:
        if delivery_attempt.get("mode") == "attended":
            result.update({
                "status": "idle",
                "phase": "attended_shadow",
                "promotion_expected": False,
                "summary": "attended shadow validated; production is unchanged",
                "next_action": "review the run/hash before explicit attended promotion",
            })
            return result, sorted(set(issues))
        finished = delivery_attempt.get("finished_at")
        age_h = age_seconds(str(finished)) / 3600 if finished else None
        if age_h is not None and age_h <= 1:
            result.update({
                "status": "pending",
                "phase": "promotion",
                "promotion_expected": True,
                "summary": "validated candidate is awaiting its correlated promotion result",
                "next_action": "promotion transaction in progress or queued",
            })
            return result, sorted(set(issues))
        result.update({
            "status": "failed",
            "phase": "promotion",
            "promotion_expected": True,
            "last_attempt": {
                "outcome": "failure",
                "run_id": delivery_attempt.get("run_id"),
                "database_sha256": delivery_attempt.get("database_sha256"),
                "finished_at": promotion_job.get("last_ok_at")
                or delivery_attempt.get("finished_at"),
                "failure_code": promotion_job.get("failure_code")
                or "promotion_record_mismatch",
                "context": {"phase": "promotion"},
            },
            "summary": "promotion result does not match the validated candidate",
            "next_action": "inspect the correlated promotion job",
        })
        issues.append("job:timetable-automation")
        return result, sorted(set(issues))

    result["last_attempt"] = promotion_attempt
    outcome = promotion_attempt.get("outcome")
    if outcome in {"accepted", "no_change"}:
        result.update({
            "status": "healthy",
            "phase": "complete",
            "promotion_expected": False,
            "summary": "latest validated candidate completed safely",
            "next_action": "next scheduled due check",
        })
    elif outcome == "running":
        result.update({
            "status": "pending",
            "phase": "promotion",
            "promotion_expected": True,
            "summary": "correlated promotion transaction is running",
            "next_action": "wait for the bounded transaction",
        })
    else:
        result.update({
            "status": "failed",
            "phase": "promotion",
            "promotion_expected": False,
            "summary": "candidate promotion was refused or rolled back",
            "next_action": "review the recorded failure before another candidate",
        })
        issues.append("job:timetable-automation")
    return result, sorted(set(issues))


def editorial_refresh_check() -> tuple[dict, list[str]]:
    enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-editorial-refresh.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    if not enabled:
        return {"status": "disabled"}, ["job:editorial-refresh"]
    result: dict[str, object] = {"status": "enabled"}
    issues: list[str] = []
    for name in ("editorial-fetch", "editorial-promote"):
        path = STATE / "jobs" / f"{name}.json"
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            last_result = job.get("last_result")
            last_ok = (job.get("last_skipped_at")
                       if last_result == "skipped"
                       else job.get("last_success_at"))
            age_h = age_seconds(last_ok) / 3600 if last_ok else None
            result[name] = {
                "result": last_result,
                "last_ok_at": last_ok,
                "age_hours": round(age_h, 2) if age_h is not None else None,
            }
            if last_result == "failure" or age_h is None or age_h > 2:
                issues.append(f"job:{name}")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            result[name] = {"result": "missing", "error": str(exc)}
            issues.append(f"job:{name}")
    try:
        attempt = json.loads(EDITORIAL_STATE.read_text(encoding="utf-8"))
        result["last_attempt"] = attempt
        outcome = attempt.get("outcome")
        finished = attempt.get("finished_at")
        age_h = age_seconds(finished) / 3600 if finished else None
        if outcome not in {"accepted", "no_change"} \
                or age_h is None or age_h > 2:
            issues.append("job:editorial-promote")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["last_attempt"] = {"outcome": "missing", "error": str(exc)}
        issues.append("job:editorial-promote")
    return result, issues


def _fleet_job(name: str) -> dict[str, object]:
    path = STATE / "jobs" / f"{name}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("job record is not an object")
        last_result = value.get("last_result")
        last_ok = (value.get("last_skipped_at")
                   if last_result == "skipped"
                   else value.get("last_success_at"))
        last_finished = value.get("last_finished_at")
        age_from = last_ok or last_finished
        age_h = age_seconds(str(age_from)) / 3600 if age_from else None
        return {
            "result": last_result,
            "last_ok_at": last_ok,
            "last_finished_at": last_finished,
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "failure_code": value.get("failure_code"),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"result": "missing", "error": type(exc).__name__}


def fleet_automation_check() -> tuple[dict, list[str]]:
    """Summarise the low-touch weekly fleet refresh as one health contract."""
    if not FLEET_REFRESH_MARKER.exists() \
            and not FLEET_REFRESH_MARKER.is_symlink():
        return {"status": "disabled"}, []
    if FLEET_REFRESH_MARKER.is_symlink() or not FLEET_REFRESH_MARKER.is_file():
        return {
            "status": "failed",
            "failure_code": "unsafe_enable_marker",
        }, ["job:fleet-automation"]

    timer_enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-fleet-refresh.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    timer_active = subprocess.run(
        ["systemctl", "is-active", "bbb-fleet-refresh.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    result: dict[str, object] = {
        "status": "pending",
        "timer": {"enabled": timer_enabled, "active": timer_active},
    }
    if not timer_enabled or not timer_active:
        result.update({
            "status": "failed",
            "failure_code": "timer_not_running",
        })
        return result, ["job:fleet-automation"]

    jobs = {
        name: _fleet_job(name)
        for name in ("fleet-refresh", "fleet-stage", "enrichment-promote-fleet")
    }
    result["jobs"] = jobs
    if any(job.get("result") == "missing" for job in jobs.values()):
        if any(job.get("result") == "failure" for job in jobs.values()):
            result.update({
                "status": "failed",
                "failure_code": "refresh_or_promotion_failed",
            })
            return result, ["job:fleet-automation"]
        result["summary"] = "enabled; waiting for the first commissioned run"
        return result, []

    try:
        promotion = json.loads(FLEET_PROMOTION_STATE.read_text(encoding="utf-8"))
        shadow = json.loads(FLEET_SHADOW_REPORT.read_text(encoding="utf-8"))
        if not isinstance(promotion, dict) or not isinstance(shadow, dict):
            raise ValueError("fleet state has the wrong shape")
        difference = shadow.get("difference")
        difference = difference if isinstance(difference, dict) else {}
        candidate = shadow.get("candidate")
        candidate = candidate if isinstance(candidate, dict) else {}
        candidate_summary = candidate.get("summary")
        candidate_summary = (candidate_summary
                             if isinstance(candidate_summary, dict) else {})
        live = shadow.get("live")
        live = live if isinstance(live, dict) else {}
        live_summary = live.get("summary")
        live_summary = live_summary if isinstance(live_summary, dict) else {}
        promoted_candidate = promotion.get("candidate")
        promoted_candidate = (promoted_candidate
                              if isinstance(promoted_candidate, dict) else {})
        result["last_attempt"] = {
            "outcome": promotion.get("outcome"),
            "finished_at": promotion.get("finished_at"),
            "error": promotion.get("error"),
            "recovery_healthy": promotion.get("recovery_healthy"),
            "candidate_sha256": candidate.get("sha256"),
            "promoted_candidate_sha256": promoted_candidate.get("sha256"),
            "live_sha256_before": live.get("sha256"),
            "candidate_records": candidate_summary.get("records"),
            "live_records_before": live_summary.get("records"),
            "added": difference.get("added"),
            "removed": difference.get("removed"),
            "changed": difference.get("changed"),
            "operator_transitions": shadow.get("operator_transitions", []),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result.update({
            "status": "failed",
            "failure_code": "missing_detail",
            "error": type(exc).__name__,
        })
        return result, ["job:fleet-automation"]

    refresh = jobs["fleet-refresh"]
    stage = jobs["fleet-stage"]
    promote = jobs["enrichment-promote-fleet"]
    attempt = result["last_attempt"]
    attempt = attempt if isinstance(attempt, dict) else {}
    outcome = attempt.get("outcome")
    digests_match = (
        isinstance(attempt.get("candidate_sha256"), str)
        and attempt.get("candidate_sha256")
        == attempt.get("promoted_candidate_sha256")
    )
    ages = [job.get("age_hours") for job in jobs.values()]
    if refresh.get("result") != "success":
        failed_phase = "refresh"
    elif stage.get("result") != "success":
        failed_phase = "stage"
    elif promote.get("result") not in {"success", "skipped"}:
        failed_phase = "promotion"
    elif not digests_match:
        failed_phase = "correlation"
    elif any(not isinstance(age, (int, float))
             or age > FLEET_MAX_AGE_HOURS for age in ages):
        failed_phase = "overdue"
    else:
        failed_phase = None
    failed = (
        failed_phase is not None
        or outcome not in {"accepted", "no_change"}
    )
    if failed:
        failed_phase = failed_phase or "promotion"
        if failed_phase in {"refresh", "stage"}:
            failed_job = jobs[f"fleet-{failed_phase}"]
            attempt.update({
                "outcome": "failure",
                "finished_at": failed_job.get("last_finished_at"),
                "error": failed_job.get("failure_code"),
            })
        result.update({
            "status": "failed",
            "phase": failed_phase,
            "failure_code": (
                "safe_rollback" if outcome == "rolled_back"
                else "refresh_or_promotion_failed"),
        })
        return result, ["job:fleet-automation"]
    result.update({
        "status": "healthy",
        "summary": "latest weekly fleet refresh completed safely",
    })
    return result, []


def locality_automation_check() -> tuple[dict, list[str]]:
    """Summarise the timetable-triggered locality refresh transaction."""
    if not LOCALITY_REFRESH_MARKER.exists() \
            and not LOCALITY_REFRESH_MARKER.is_symlink():
        return {"status": "disabled"}, []
    if LOCALITY_REFRESH_MARKER.is_symlink() \
            or not LOCALITY_REFRESH_MARKER.is_file():
        return {
            "status": "failed",
            "failure_code": "unsafe_enable_marker",
        }, ["job:locality-automation"]
    timetable_enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-timetable-shadow.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    if not timetable_enabled:
        return {"status": "disabled_with_timetable"}, []
    jobs = {
        name: _fleet_job(name)
        for name in (
            "locality-refresh", "locality-stage",
            "enrichment-promote-localities")
    }
    result: dict[str, object] = {
        "status": "pending",
        "trigger": "successful timetable promotion check",
        "jobs": jobs,
    }
    if any(job.get("result") == "missing" for job in jobs.values()):
        if any(job.get("result") == "failure" for job in jobs.values()):
            result.update({
                "status": "failed",
                "failure_code": "refresh_or_promotion_failed",
            })
            return result, ["job:locality-automation"]
        result["summary"] = "waiting for the first commissioned run"
        return result, []
    try:
        promotion = json.loads(
            LOCALITY_PROMOTION_STATE.read_text(encoding="utf-8"))
        shadow = json.loads(LOCALITY_SHADOW_REPORT.read_text(encoding="utf-8"))
        candidate = shadow.get("candidate")
        candidate = candidate if isinstance(candidate, dict) else {}
        promoted = promotion.get("candidate")
        promoted = promoted if isinstance(promoted, dict) else {}
        coverage = shadow.get("coverage")
        coverage = coverage if isinstance(coverage, dict) else {}
        result["last_attempt"] = {
            "outcome": promotion.get("outcome"),
            "finished_at": promotion.get("finished_at"),
            "candidate_sha256": candidate.get("sha256"),
            "promoted_candidate_sha256": promoted.get("sha256"),
            "records": (candidate.get("summary") or {}).get("records")
            if isinstance(candidate.get("summary"), dict) else None,
            "coverage": coverage,
            "boundary": shadow.get("boundary"),
            "error": promotion.get("error"),
            "recovery_healthy": promotion.get("recovery_healthy"),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result.update({
            "status": "failed",
            "failure_code": "missing_detail",
            "error": type(exc).__name__,
        })
        return result, ["job:locality-automation"]
    refresh = jobs["locality-refresh"]
    stage = jobs["locality-stage"]
    promote = jobs["enrichment-promote-localities"]
    attempt = result["last_attempt"]
    attempt = attempt if isinstance(attempt, dict) else {}
    ages = [job.get("age_hours") for job in jobs.values()]
    failed = (
        refresh.get("result") != "success"
        or stage.get("result") != "success"
        or promote.get("result") not in {"success", "skipped"}
        or attempt.get("outcome") not in {"accepted", "no_change"}
        or not isinstance(attempt.get("candidate_sha256"), str)
        or attempt.get("candidate_sha256")
        != attempt.get("promoted_candidate_sha256")
        or any(not isinstance(age, (int, float))
               or age > LOCALITY_MAX_AGE_HOURS for age in ages)
        or (attempt.get("coverage") or {}).get("missing") != 0
        or (attempt.get("coverage") or {}).get("extra") != 0
    )
    if failed:
        result.update({
            "status": "failed",
            "failure_code": "refresh_or_promotion_failed",
        })
        return result, ["job:locality-automation"]
    result.update({
        "status": "healthy",
        "summary": "latest timetable-triggered locality refresh completed safely",
    })
    return result, []


def blurb_generation_check() -> tuple[dict, list[str]]:
    """Summarise safe weekly generation and the attended review queue."""
    if not BLURB_GENERATION_MARKER.exists() \
            and not BLURB_GENERATION_MARKER.is_symlink():
        return {"status": "disabled"}, []
    if BLURB_GENERATION_MARKER.is_symlink() \
            or not BLURB_GENERATION_MARKER.is_file():
        return {
            "status": "failed", "failure_code": "unsafe_enable_marker",
        }, ["job:blurb-generation"]
    timer_enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-blurb-generate.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    timer_active = subprocess.run(
        ["systemctl", "is-active", "bbb-blurb-generate.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    result: dict[str, object] = {
        "status": "pending",
        "timer": {"enabled": timer_enabled, "active": timer_active},
    }
    if not timer_enabled or not timer_active:
        result.update({"status": "failed",
                       "failure_code": "timer_not_running"})
        return result, ["job:blurb-generation"]

    job = _fleet_job("blurb-generate")
    result["job"] = job
    try:
        if BLURB_PENDING.is_symlink():
            raise ValueError("pending path is a symlink")
        if BLURB_PENDING.is_file():
            pending = json.loads(BLURB_PENDING.read_text(encoding="utf-8"))
            additions = pending.get("additions")
            if pending.get("status") != "pending_review" \
                    or not isinstance(additions, dict):
                raise ValueError("pending batch has the wrong shape")
            keys = set()
            lines = 0
            for values in additions.values():
                if not isinstance(values, dict):
                    raise ValueError("pending additions have the wrong shape")
                keys.update(map(str, values))
                lines += len(values)
            result["pending_review"] = {
                "batch_id": pending.get("batch_id"),
                "buses": len(keys),
                "lines": lines,
                "created_at": pending.get("created_at"),
            }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result.update({"status": "failed",
                       "failure_code": "unsafe_pending_batch",
                       "error": type(exc).__name__})
        return result, ["job:blurb-generation"]

    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    try:
        ledger = json.loads(BLURB_USAGE.read_text(encoding="utf-8"))
        if ledger.get("schema") != 1 or not isinstance(
                ledger.get("events"), list):
            raise ValueError("usage ledger has the wrong shape")
        month = utcnow().strftime("%Y-%m")
        for event in ledger["events"]:
            if not isinstance(event, dict) or event.get("month") != month:
                continue
            usage["requests"] += 1
            usage["input_tokens"] += int(
                event.get("actual_input_tokens")
                or event.get("reserved_input_tokens") or 0)
            usage["output_tokens"] += int(
                event.get("actual_output_tokens")
                or event.get("reserved_output_tokens") or 0)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result.update({"status": "failed",
                       "failure_code": "unsafe_usage_ledger",
                       "error": type(exc).__name__})
        return result, ["job:blurb-generation"]
    result["month_usage"] = usage

    if job.get("result") == "failure":
        result.update({"status": "failed",
                       "failure_code": job.get("failure_code")
                       or "generation_failed"})
        return result, ["job:blurb-generation"]
    age = job.get("age_hours")
    if job.get("result") in {"success", "skipped"} and (
            not isinstance(age, (int, float)) or age > BLURB_MAX_AGE_HOURS):
        result.update({"status": "failed", "failure_code": "overdue"})
        return result, ["job:blurb-generation"]
    if "pending_review" in result:
        result["status"] = "pending_review"
        result["summary"] = "generated text is waiting for human approval"
    elif job.get("result") in {"success", "skipped"}:
        result["status"] = "healthy"
        result["summary"] = "weekly check completed with no pending batch"
    else:
        result["summary"] = "enabled; waiting for the first attended run"
    return result, []


def social_curation_check() -> tuple[dict, list[str]]:
    enabled = subprocess.run(
        ["systemctl", "is-enabled", "bbb-social-curation.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False).returncode == 0
    configured = SOCIAL_CONFIG.is_file() and SOCIAL_TOKEN.is_file()
    result: dict[str, object] = {
        "status": "enabled" if enabled else (
            "configured_disabled" if configured else "not_configured"),
        "mode": "live" if SOCIAL_LIVE_MARKER.is_file() else "shadow",
    }
    issues: list[str] = []
    if enabled and not configured:
        issues.append("credential:social-curation")
    if enabled:
        path = STATE / "jobs/social-curation.json"
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            success = job.get("last_success_at")
            age_h = age_seconds(success) / 3600 if success else None
            result["job"] = {
                "result": job.get("last_result"),
                "last_success_at": success,
                "age_hours": round(age_h, 2) if age_h is not None else None,
            }
            if (job.get("last_result") == "failure"
                    or age_h is None or age_h > 1):
                issues.append("job:social-curation")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            result["job"] = {"result": "missing", "error": str(exc)}
            issues.append("job:social-curation")
    try:
        connection = sqlite3.connect(
            f"file:{SOCIAL_DB}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM deliveries GROUP BY status"
            ).fetchall()
            last_delivery = connection.execute(
                "SELECT MAX(updated_at) FROM deliveries WHERE status='delivered'"
            ).fetchone()[0]
        finally:
            connection.close()
        result["deliveries"] = {
            "by_status": {str(status): int(count) for status, count in rows},
            "last_delivered_at": last_delivery,
        }
    except (OSError, sqlite3.Error):
        result["deliveries"] = {"by_status": {}, "last_delivered_at": None}
    return result, issues


def data_health_check() -> tuple[dict, list[str]]:
    """Expose the report-only data audit without opening incidents for findings."""
    try:
        report = json.loads(DATA_HEALTH_REPORT.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("schema_version") != 1:
            raise ValueError("unsupported data-health report")
        generated_at = report.get("generated_at")
        age_h = age_seconds(str(generated_at)) / 3600 if generated_at else None
        result = dict(report)
        result["age_hours"] = round(age_h, 2) if age_h is not None else None
        if age_h is None or age_h > JOB_MAX_AGE_HOURS["data-health"]:
            result["status"] = "stale"
        # Completeness findings stay report-only. A missing/failed/stale job is
        # still caught by job_checks through its recorded wrapper state.
        return result, []
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"status": "unavailable", "error": type(exc).__name__}, []


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except OSError:
        return False


def notify(text: str) -> bool:
    try:
        url = WEBHOOK.read_text(encoding="utf-8").splitlines()[0].strip()
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
            return False
        request = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
            return 200 <= int(response.status) < 300
    except (OSError, ValueError):
        return False


def _display_time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BRISTOL_TZ).strftime("%d %B %Y at %H:%M %Z")
    except ValueError:
        return str(value or "unknown time")


def _run_line(run_id: object) -> str:
    value = str(run_id or "")
    if value.isdigit():
        return f"GitHub build: <{TIMETABLE_RUN_URL.format(value)}|run {value}>"
    return "GitHub build: unavailable"


def _service_date(value: object) -> str:
    try:
        parsed = datetime.strptime(str(value), "%Y%m%d")
        return f"{parsed.day} {parsed.strftime('%B %Y')}"
    except ValueError:
        return str(value or "unknown")


def timetable_success_message(attempt: dict[str, object]) -> str:
    validation = attempt.get("validation")
    counts = validation if isinstance(validation, dict) else {}

    def count(name: str) -> str:
        try:
            return f"{int(counts[name]):,}"
        except (KeyError, TypeError, ValueError):
            return "?"

    if attempt.get("tnds_status") == "fallback_used":
        source = "TNDS fallback used"
    elif attempt.get("tnds_status") == "not_needed":
        source = "BODS and First sources complete; TNDS not needed"
    else:
        source = "source decision unavailable"
    digest = str(attempt.get("database_sha256") or "")[:12] or "unknown"
    duration = attempt.get("duration_seconds")
    duration_text = (f"{float(duration):.0f}s"
                     if isinstance(duration, (int, float)) else "unknown")
    return "\n".join((
        ":white_check_mark: *Timetable updated automatically*",
        f"Installed: {_display_time(attempt.get('finished_at'))}",
        f"Coverage: through {_service_date(counts.get('latest_service'))}",
        "Contents: "
        f"{count('routes')} routes · {count('trips')} trips · "
        f"{count('stops')} stops · {count('stop_times')} stop times · "
        f"{count('route_shapes')} route shapes",
        f"Stop-search lookup: {count('stop_routes')} stop/route pairs",
        "Edition safety: "
        f"{count('superseded_route_editions')} overlapping route editions "
        "given separate effective windows",
        f"Sources: {source}",
        f"Safety: stop search, collector, site, bot and public health passed; "
        f"previous timetable retained for rollback",
        f"Database: {digest} · promotion {duration_text}",
        _run_line(attempt.get("run_id")),
    ))


def timetable_failure_message(kind: str,
                              attempt: dict[str, object]) -> str:
    code = str(attempt.get("failure_code") or "unknown_failure")
    context = attempt.get("context")
    context = context if isinstance(context, dict) else {}
    safe_parts = []
    for key in (
            "phase", "metric", "date", "operator", "current",
            "candidate", "minimum", "policy_version"):
        value = context.get(key)
        if isinstance(value, (str, int, float)) and value != "":
            safe_parts.append(f"{key}={value}")
    reason = ", ".join(safe_parts) or "See the allowlisted job record"
    outcome = str(attempt.get("outcome") or "failure")
    if kind == "shadow":
        safety = (
            "The candidate never reached production; the existing timetable "
            "remains live. The Pi will try a fresh delivery at its next due check.")
        title = ":rotating_light: *Timetable build/delivery failed*"
    elif outcome == "rolled_back" and attempt.get("recovery_healthy") is True:
        safety = (
            "The previous timetable was restored and all consumer health "
            "checks passed. This rejected candidate is blocked from replay.")
        title = ":rotating_light: *Timetable promotion failed and rolled back*"
    elif outcome == "rollback_failed":
        safety = (
            "Automatic recovery could not prove every service healthy; "
            "manual attention is required urgently.")
        title = ":rotating_light: *URGENT: timetable rollback not healthy*"
    else:
        safety = (
            "The candidate was rejected before acceptance; the existing "
            "timetable remains the production version.")
        title = ":rotating_light: *Timetable promotion rejected*"
    return "\n".join((
        title,
        f"When: {_display_time(attempt.get('finished_at'))}",
        f"Failure: `{code}`",
        f"Reason: {reason[:300]}",
        f"Safety: {safety}",
        _run_line(attempt.get("run_id")),
    ))


def editorial_success_message(attempt: dict[str, object]) -> str:
    content = attempt.get("content")
    counts = content if isinstance(content, dict) else {}
    return "\n".join((
        ":white_check_mark: *Approved bot information updated*",
        f"Installed: {_display_time(attempt.get('finished_at'))}",
        "Contents: "
        f"{counts.get('facts', '?')} sourced facts · "
        f"{counts.get('occasions', '?')} calendar items · "
        f"{counts.get('news', '?')} active or expiring news items",
        "Safety: schema, source allowlist and expiry rules passed; "
        "the bot restarted with the exact approved file.",
        f"GitHub approval source: <{EDITORIAL_FILE_URL}|editorial context on main>",
    ))


def editorial_failure_message(refresh: dict[str, object]) -> str:
    attempt = refresh.get("last_attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    fetch = refresh.get("editorial-fetch")
    promote = refresh.get("editorial-promote")
    return "\n".join((
        ":rotating_light: *Bot information refresh failed*",
        f"When checked: {_display_time(utcnow().isoformat())}",
        f"Fetch: {fetch}",
        f"Promotion: {promote}",
        f"Last outcome: {attempt.get('outcome', 'unknown')}",
        f"Reason: {str(attempt.get('error') or 'See the recorded job and journal')[:300]}",
        "Safety: the previously approved information remains live; "
        "an unvalidated or unhealthy update was not accepted.",
    ))


def fleet_failure_message(refresh: dict[str, object]) -> str:
    attempt = refresh.get("last_attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    outcome = str(attempt.get("outcome") or "not completed")
    if outcome == "rolled_back" and attempt.get("recovery_healthy") is True:
        safety = "The previous fleet data was restored and is healthy."
    elif outcome == "rollback_failed":
        safety = (
            "Automatic rollback was not proven healthy; please check this now.")
    else:
        safety = (
            "The candidate was not accepted, so the existing fleet data remains live.")
    return "\n".join((
        ":rotating_light: *Fleet information refresh needs attention*",
        f"When checked: {_display_time(utcnow().isoformat())}",
        f"Result: {outcome}",
        f"Reason: {refresh.get('failure_code', 'see the recorded job')}",
        f"Safety: {safety}",
        "You do not need to check routine successful runs; they appear in "
        "the estate digest.",
    ))


def main() -> int:
    issues: list[str] = []
    services, found = service_checks()
    issues.extend(found)
    jobs, found = job_checks()
    issues.extend(found)
    timetable_automation, found = timetable_automation_check()
    issues.extend(found)
    timetable_delivery = timetable_automation.get("delivery")
    timetable_delivery = timetable_delivery if isinstance(
        timetable_delivery, dict) else {}
    timetable_promotion = timetable_automation.get("promotion")
    timetable_promotion = timetable_promotion if isinstance(
        timetable_promotion, dict) else {}
    fleet_automation, found = fleet_automation_check()
    issues.extend(found)
    locality_automation, found = locality_automation_check()
    issues.extend(found)
    blurb_generation, found = blurb_generation_check()
    issues.extend(found)
    editorial_refresh, found = editorial_refresh_check()
    issues.extend(found)
    social_deliveries, found = social_curation_check()
    issues.extend(found)
    data_health, found = data_health_check()
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
        "timetable_automation": timetable_automation,
        "fleet_automation": fleet_automation,
        "locality_automation": locality_automation,
        "blurb_generation": blurb_generation,
        "editorial_refresh": editorial_refresh,
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
        "social_deliveries": social_deliveries,
        "data_health": data_health,
        "resource_samples_age_minutes": round(resource_age, 1)
        if resource_age is not None else None,
    }
    atomic_json(STATE / "health.json", snapshot)

    incident_path = STATE / "incidents.json"
    try:
        previous = json.loads(incident_path.read_text(encoding="utf-8"))
        previous_issues = set(previous.get("active", []))
    except (OSError, json.JSONDecodeError):
        previous = {}
        previous_issues = set()
    current = set(unique_issues)
    opened = sorted(current - previous_issues)
    resolved = sorted(previous_issues - current)
    notified_run = str(previous.get("last_timetable_success_run_id", ""))
    automation_attempt = timetable_automation.get("last_attempt")
    if not isinstance(automation_attempt, dict):
        automation_attempt = {}
    accepted_run = str(automation_attempt.get("run_id") or "")
    sent_timetable_success = False
    if automation_attempt.get("outcome") == "accepted" \
            and accepted_run.isdigit() and accepted_run != notified_run:
        if notify(timetable_success_message(automation_attempt)):
            notified_run = accepted_run
            sent_timetable_success = True

    editorial_attempt = editorial_refresh.get("last_attempt")
    if not isinstance(editorial_attempt, dict):
        editorial_attempt = {}
    notified_editorial_blob = str(
        previous.get("last_editorial_success_blob_sha", ""))
    accepted_editorial_blob = str(editorial_attempt.get("blob_sha") or "")
    if editorial_attempt.get("outcome") == "accepted" \
            and accepted_editorial_blob \
            and accepted_editorial_blob != notified_editorial_blob:
        if notify(editorial_success_message(editorial_attempt)):
            notified_editorial_blob = accepted_editorial_blob

    remaining_opened = list(opened)
    notified_failures = previous.get("notified_timetable_failure_fingerprints")
    if not isinstance(notified_failures, list):
        notified_failures = []
    notified_failures = [str(value) for value in notified_failures][-99:]
    if timetable_automation.get("status") == "failed":
        phase = str(timetable_automation.get("phase") or "shadow")
        fingerprint = "|".join((
            phase,
            str(automation_attempt.get("run_id") or ""),
            str(automation_attempt.get("finished_at") or ""),
            str(automation_attempt.get("failure_code") or "unknown_failure"),
            str(automation_attempt.get("database_sha256") or ""),
        ))
        if fingerprint not in notified_failures and notify(
                timetable_failure_message(phase, automation_attempt)):
            notified_failures.append(fingerprint)
        if "job:timetable-automation" in remaining_opened:
            remaining_opened.remove("job:timetable-automation")
    editorial_issues = {
        "job:editorial-refresh",
        "job:editorial-fetch",
        "job:editorial-promote",
    }.intersection(remaining_opened)
    if editorial_issues:
        notify(editorial_failure_message(editorial_refresh))
        remaining_opened = [
            issue for issue in remaining_opened
            if issue not in editorial_issues
        ]
    if "job:fleet-automation" in remaining_opened:
        notify(fleet_failure_message(fleet_automation))
        remaining_opened.remove("job:fleet-automation")
    if remaining_opened:
        notify(":rotating_light: BBB health incident: " + ", ".join(remaining_opened))

    timetable_resolved = "job:timetable-automation" in resolved
    recovery_pending = bool(
        previous.get("timetable_recovery_pending") or timetable_resolved)
    if recovery_pending and timetable_automation.get("status") in {
            "healthy", "idle"}:
        if sent_timetable_success:
            recovery_pending = False
        else:
            recovery_pending = not notify(
                ":white_check_mark: *Timetable automation recovered*\n"
                "The latest timetable check completed safely and the existing "
                "production services are healthy.")
    remaining_resolved = [issue for issue in resolved
                          if issue != "job:timetable-automation"]
    if remaining_resolved:
        notify(":white_check_mark: BBB health recovery: "
               + ", ".join(remaining_resolved))
    atomic_json(incident_path, {
        "updated_at": utcnow().isoformat(),
        "active": unique_issues,
        "last_timetable_success_run_id": notified_run,
        "notified_timetable_failure_fingerprints": notified_failures,
        "timetable_recovery_pending": recovery_pending,
        "last_editorial_success_blob_sha": notified_editorial_blob,
    })
    print(json.dumps({"status": snapshot["status"], "issues": unique_issues}))
    return 1 if unique_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
