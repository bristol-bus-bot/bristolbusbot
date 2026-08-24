#!/usr/bin/env python3
"""One plain-English daily Slack update for the person running the bot.

Detailed metrics remain in the private health and job records. Slack answers
four human questions instead: is everything working, what changed, what is
still on the agreed plan, and does Tom need to do anything. Routine successes
are folded into this message; failures still alert immediately.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

HOME = Path.home()
LIVE_DB = Path(os.getenv(
    "BBB_LIVE_DB", "/var/lib/bristolbusbot/collector/live.db"))
WEBHOOK_CONF = HOME / ".config" / "busbot-alerts" / "webhook"
BOT_DB = Path(os.getenv(
    "BBB_BOT_DB", "/var/lib/bristolbusbot/bot/app_data.db"))
SITE_URL = "http://127.0.0.1:5002/healthz"
AGGREGATE_HEALTH = Path(os.getenv(
    "BBB_AGGREGATE_HEALTH",
    "/var/lib/bristolbusbot/monitoring/health.json"))
DAILY_STATE = Path(os.getenv(
    "BBB_DAILY_DIGEST_STATE",
    "/var/lib/bristolbusbot/monitoring/daily-digest-state.json"))
CURRENT_RELEASES = Path(os.getenv(
    "BBB_CURRENT_RELEASES", str(HOME / "bristolbusbot" / "current")))


def _post(text: str) -> bool:
    if not WEBHOOK_CONF.exists():
        print("no webhook config; printing instead:\n" + text)
        return False
    url = WEBHOOK_CONF.read_text().strip().splitlines()[0].strip()
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()
    return True


def collector_lines() -> list[str]:
    try:
        conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        total = conn.execute(
            "SELECT COUNT(*) FROM vehicles WHERE updated_at > ?", (cutoff,)
        ).fetchone()[0]
        matched = conn.execute(
            "SELECT COUNT(*) FROM vehicles WHERE updated_at > ? "
            "AND trip_id IS NOT NULL", (cutoff,)).fetchone()[0]
        # matched but delay NULL: schedule found, every reading refused by
        # the distance gates -> likely a wrong-schedule match
        suspect = conn.execute(
            "SELECT COUNT(*) FROM vehicles WHERE updated_at > ? "
            "AND trip_id IS NOT NULL AND delay_seconds IS NULL "
            "AND at_depot IS NOT 1", (cutoff,)).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(updated_at) FROM vehicles").fetchone()[0]
        age = "?"
        if newest:
            dt = datetime.fromisoformat(newest)
            age = f"{(datetime.now(timezone.utc) - dt).total_seconds():.0f}s"
        rate = f"{matched}/{total}" + (f" ({matched / total:.0%})" if total else "")
        flag = " :warning:" if total and suspect / max(matched, 1) > 0.15 else ""
        return [f"*collector*  freshest data {age} old · {total} active · matched {rate}",
                f"*matching*  {suspect} matched-but-ungated (mismatch canary){flag}"]
    except Exception as e:  # noqa: BLE001 - digest must survive any probe
        return [f"*collector*  probe failed: {e}"]


def bot_line() -> str:
    try:
        if not BOT_DB.exists():
            return "*bot*  durable delivery database not found"
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(f"file:{BOT_DB}?mode=ro", uri=True)
        try:
            real = conn.execute(
                "SELECT COUNT(*) FROM engagement_analytics "
                "WHERE post_uri IS NOT NULL AND substr(timestamp,1,10)=?",
                (today,),
            ).fetchone()[0]
        finally:
            conn.close()
        return f"*bot*  {real} post(s) to Bluesky today"
    except Exception as e:  # noqa: BLE001
        return f"*bot*  probe failed: {e}"


def site_line() -> str:
    try:
        with urllib.request.urlopen(SITE_URL, timeout=10) as r:
            body = r.read(200).decode(errors="replace")
        return f"*site*  :white_check_mark: healthz {r.status} — {body.strip()[:80]}"
    except Exception as e:  # noqa: BLE001
        return f"*site*  :x: {e}"


def timetable_line() -> str:
    """Read only the aggregate health contract, never raw timetable job files."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        automation = snapshot.get("timetable_automation")
        if not isinstance(automation, dict):
            return "*timetable*  aggregate status unavailable"
        accepted = automation.get("last_accepted")
        accepted = accepted if isinstance(accepted, dict) else {}
        attempt = automation.get("last_attempt")
        attempt = attempt if isinstance(attempt, dict) else {}
        accepted_run = str(accepted.get("run_id") or "none")
        accepted_at = str(accepted.get("accepted_at") or "unknown")[:10]
        attempted_run = str(attempt.get("run_id") or "none")
        outcome = str(attempt.get("outcome") or automation.get("status") or "unknown")
        next_action = str(automation.get("next_action") or "next scheduled check")
        return (
            f"*timetable*  accepted run {accepted_run} ({accepted_at}) - "
            f"last run {attempted_run}: {outcome} - {next_action}"
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*timetable*  aggregate probe failed: {type(exc).__name__}"


def social_line() -> str:
    """Read the curation status from the same aggregate health contract."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        social = snapshot.get("social_deliveries")
        if not isinstance(social, dict):
            return "*social*  aggregate status unavailable"
        status = str(social.get("status") or "unknown")
        mode = str(social.get("mode") or "shadow")
        deliveries = social.get("deliveries")
        deliveries = deliveries if isinstance(deliveries, dict) else {}
        by_status = deliveries.get("by_status")
        by_status = by_status if isinstance(by_status, dict) else {}
        delivered = int(by_status.get("delivered", 0))
        return f"*social*  {status} - {mode} mode - {delivered} card(s) delivered"
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*social*  aggregate probe failed: {type(exc).__name__}"


def fleet_line() -> str:
    """Render the single low-touch fleet automation result."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        automation = snapshot.get("fleet_automation")
        if not isinstance(automation, dict):
            return "*fleet*  aggregate status unavailable"
        status = str(automation.get("status") or "unknown")
        if status == "disabled":
            return "*fleet*  weekly safe refresh is off"
        if status == "pending":
            return "*fleet*  weekly safe refresh enabled - first run pending"
        attempt = automation.get("last_attempt")
        attempt = attempt if isinstance(attempt, dict) else {}
        outcome = str(attempt.get("outcome") or "unknown")
        finished = str(attempt.get("finished_at") or "unknown")[:10]
        before = attempt.get("live_records_before")
        after = attempt.get("candidate_records")
        added = attempt.get("added")
        removed = attempt.get("removed")
        changed = attempt.get("changed")
        flag = ":white_check_mark:" if status == "healthy" else ":warning:"
        return (
            f"*fleet*  {flag} {outcome} ({finished}) - records {before}->{after} - "
            f"+{added}/-{removed}/{changed} changed"
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*fleet*  aggregate probe failed: {type(exc).__name__}"


def data_health_line() -> str:
    """Render the bounded, report-only data-health result from aggregate health."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        report = snapshot.get("data_health")
        if not isinstance(report, dict):
            return "*data*  report unavailable"
        status = str(report.get("status") or "unavailable")
        if status in {"unavailable", "stale"}:
            return f"*data*  :warning: report {status}"
        summary = report.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        observed = int(summary.get("observed_identities", 0))
        if status == "clean":
            age = float(summary.get("fleet_age_days", 0))
            return (f"*data*  :white_check_mark: {observed} recent buses checked"
                    f" - fleet file {age:.1f}d old - report-only")

        collapses = int(summary.get("operator_collapses", 0))
        if collapses:
            fleet = report.get("fleet")
            fleet = fleet if isinstance(fleet, dict) else {}
            details = fleet.get("operator_collapses")
            details = details if isinstance(details, list) else []
            first = details[0] if details and isinstance(details[0], dict) else {}
            operator = str(first.get("operator") or "operator")
            previous = int(first.get("previous", 0))
            current = int(first.get("current", 0))
            return (f"*data*  :warning: {operator} fleet count collapsed "
                    f"{previous}->{current} - report-only")

        missing_fleet = int(summary.get("missing_fleet", 0))
        current_missing_fleet = int(
            summary.get("current_missing_fleet", missing_fleet))
        missing_livery = int(summary.get("missing_livery", 0))
        current_missing_livery = int(
            summary.get("current_missing_livery", missing_livery))
        thresholds = report.get("thresholds")
        thresholds = thresholds if isinstance(thresholds, dict) else {}
        observed_days = int(thresholds.get("observed_days", 56))
        current_hours = int(thresholds.get("current_vehicle_hours", 24))
        missing_stops = int(summary.get("missing_stop_localities", 0))
        blurbs = summary.get("missing_blurbs")
        blurbs = blurbs if isinstance(blurbs, dict) else {}
        in_service = int(blurbs.get("in_service", 0))
        waiting = int(blurbs.get("waiting", 0))
        depot = int(blurbs.get("depot", 0))
        return (
            f"*data*  :information_source: {missing_fleet} identities unmatched "
            f"across {observed_days}d ({current_missing_fleet} in the last "
            f"{current_hours}h) - {missing_livery} source livery gaps "
            f"({current_missing_livery} current) - blurb gaps "
            f"{in_service}/{waiting}/{depot} (service/wait/depot) - "
            f"{missing_stops} locality gaps - report-only"
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*data*  aggregate probe failed: {type(exc).__name__}"


def blurb_line() -> str:
    """Show only whether a review is waiting and this month's bounded use."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        generation = snapshot.get("blurb_generation")
        if not isinstance(generation, dict):
            return "*blurbs*  aggregate status unavailable"
        status = str(generation.get("status") or "unknown")
        if status == "disabled":
            return "*blurbs*  weekly missing-description check is off"
        usage = generation.get("month_usage")
        usage = usage if isinstance(usage, dict) else {}
        requests = int(usage.get("requests", 0))
        tokens = int(usage.get("input_tokens", 0)) \
            + int(usage.get("output_tokens", 0))
        if status == "pending_review":
            pending = generation.get("pending_review")
            pending = pending if isinstance(pending, dict) else {}
            return (f"*blurbs*  {int(pending.get('buses', 0))} bus(es) "
                    f"waiting for your review - {requests} request(s), "
                    f"{tokens} token(s) this month")
        if status == "healthy":
            return (f"*blurbs*  :white_check_mark: nothing waiting - "
                    f"{requests} request(s), {tokens} token(s) this month")
        if status == "pending":
            return "*blurbs*  weekly safe generation enabled - first run pending"
        return (f"*blurbs*  :warning: {status} - "
                f"{str(generation.get('failure_code') or 'check the job record')}")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*blurbs*  aggregate probe failed: {type(exc).__name__}"


def anomaly_line() -> str:
    """Render the latest bounded collector-quality report from aggregate health."""
    try:
        snapshot = json.loads(AGGREGATE_HEALTH.read_text(encoding="utf-8"))
        report = snapshot.get("collector_anomaly")
        if not isinstance(report, dict):
            return "*anomalies*  report unavailable"
        status = str(report.get("status") or "unavailable")
        if status in {"unavailable", "stale"}:
            return f"*anomalies*  :warning: report {status}"

        coverage = report.get("coverage")
        coverage = coverage if isinstance(coverage, dict) else {}
        detectors = report.get("detectors")
        detectors = detectors if isinstance(detectors, dict) else {}
        metrics = report.get("poll_metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        older = metrics.get("older_half")
        older = older if isinstance(older, dict) else {}
        recent = metrics.get("recent_half")
        recent = recent if isinstance(recent, dict) else {}

        def detector_count(name: str) -> int:
            value = detectors.get(name)
            value = value if isinstance(value, dict) else {}
            return int(value.get("count", 0))

        def percent(value: object) -> str:
            return (f"{float(value):.1%}"
                    if isinstance(value, (int, float)) else "?")

        gps = detectors.get("gps_distance_m")
        gps = gps if isinstance(gps, dict) else {}
        counts = {
            "extreme": detector_count("extreme_delays"),
            "backwards": detector_count("backwards_stop_progress"),
            "speeds": detector_count("timetable_stop_transition_speeds")
            or detector_count("impossible_implied_speeds"),
            "overlaps": detector_count("overlapping_vehicle_trips"),
            "near_gate": detector_count("gps_near_match_gate"),
        }
        flag = ":warning:" if any(counts.values()) else ":white_check_mark:"
        return (
            f"*anomalies*  {flag} 48h/{int(coverage.get('observations', 0)):,} obs - "
            f"extreme-delay readings {counts['extreme']:,} - backwards flags "
            f"{counts['backwards']:,} - trip-overlap flags {counts['overlaps']:,} - "
            f"stop-transition speed flags {counts['speeds']:,} - near GPS gate "
            f"{counts['near_gate']:,} - "
            f"match {percent(older.get('match_rate'))}->{percent(recent.get('match_rate'))} - "
            f"GPS p95 {int(gps.get('p95', 0))}m - report-only"
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"*anomalies*  aggregate probe failed: {type(exc).__name__}"


def pi_line() -> str:
    try:
        du = shutil.disk_usage("/")
        disk = f"{du.free / 1e9:.1f}GB free of {du.total / 1e9:.0f}GB"
        temp = "?"
        tz = Path("/sys/class/thermal/thermal_zone0/temp")
        if tz.exists():
            temp = f"{int(tz.read_text().strip()) / 1000:.0f}C"
        mem = "?"
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                mem = f"{int(re.sub(r'[^0-9]', '', line)) / 1048576:.1f}GB avail"
                break
        return f"*pi*  {disk} · {mem} · cpu {temp}"
    except Exception as e:  # noqa: BLE001
        return f"*pi*  probe failed: {e}"


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain an object")
    return value


def _current_release_fingerprints() -> dict[str, str]:
    releases: dict[str, str] = {}
    for component in ("collector", "pipeline", "site", "bot", "social"):
        path = CURRENT_RELEASES / component
        try:
            releases[component] = path.resolve(strict=True).name
        except OSError:
            continue
    return releases


def _progress_fingerprints(snapshot: dict) -> dict[str, object]:
    timetable = _dict(snapshot.get("timetable_automation"))
    accepted = _dict(timetable.get("last_accepted"))
    fleet = _dict(snapshot.get("fleet_automation"))
    fleet_attempt = _dict(fleet.get("last_attempt"))
    locality = _dict(snapshot.get("locality_automation"))
    locality_attempt = _dict(locality.get("last_attempt"))
    blurbs = _dict(snapshot.get("blurb_generation"))
    blurb_job = _dict(blurbs.get("job"))
    jobs = _dict(snapshot.get("jobs"))
    audit_publish = _dict(jobs.get("audit-publish"))
    return {
        "timetable": accepted.get("run_id"),
        "fleet": fleet_attempt.get("finished_at"),
        "localities": locality_attempt.get("finished_at"),
        "descriptions": blurb_job.get("last_finished_at"),
        "audit_publish": audit_publish.get("last_success_at"),
        "releases": _current_release_fingerprints(),
    }


def _changed(current: dict, previous: dict, key: str) -> bool:
    value = current.get(key)
    return value not in (None, {}, "") and value != previous.get(key)


def progress_lines(snapshot: dict, previous_state: dict,
                   today: date | None = None) -> tuple[str, list[str]]:
    """Summarise meaningful progress without exposing release or job IDs."""
    today = today or date.today()
    previous = _dict(previous_state.get("fingerprints"))
    current = _progress_fingerprints(snapshot)
    if not previous:
        if today <= date(2026, 8, 20):
            return "Finished over the last few days", [
                "The core move away from the Windows PC is complete: timetable, "
                "fleet, stop-area and description work now runs on the Pi.",
                "Fleet details and stop areas now refresh automatically and keep "
                "the previous safe version if a new source looks wrong.",
                "Missing bus descriptions can be generated on the Pi, but they "
                "stay private until you approve them.",
                "The Instagram-card Slack workflow and the nightly checker for "
                "odd delay or matching readings are live.",
            ]
        return "Since the previous update", [
            "This is the first plain-English daily summary; no earlier summary "
            "state was available for comparison.",
        ]

    lines: list[str] = []
    if _changed(current, previous, "timetable"):
        lines.append("A new timetable passed its safety checks and went live.")
    if _changed(current, previous, "fleet"):
        lines.append("The weekly fleet and livery source refresh completed safely.")
    if _changed(current, previous, "localities"):
        lines.append("The stop-area information was checked and is up to date.")
    if _changed(current, previous, "descriptions"):
        lines.append("The Pi checked for missing bus descriptions.")
    if _changed(current, previous, "audit_publish"):
        lines.append("Yesterday's public performance report published successfully.")
    if _changed(current, previous, "releases"):
        lines.append("A software update was installed and the live checks passed.")
    if not lines:
        lines.append("No software or data change was needed; the automatic checks ran normally.")
    return "Since yesterday", lines


def overall_line(snapshot: dict) -> str:
    issues = snapshot.get("issues")
    issues = issues if isinstance(issues, list) else []
    if snapshot.get("status") == "ok" and not issues:
        return (
            ":white_check_mark: *Overall:* Everything important is working. "
            "Live buses, the website, the Bluesky bot and automatic updates "
            "are healthy."
        )
    safe_update_issues = {
        "job:timetable-automation", "job:fleet-automation",
        "job:locality-automation", "job:blurb-generation",
        "job:editorial-refresh", "job:editorial-fetch",
        "job:editorial-promote",
    }
    if issues and set(map(str, issues)).issubset(safe_update_issues):
        return (
            ":white_check_mark: *Overall:* The live bot and website are working. "
            "A proposed automatic data update was rejected safely, so the "
            "current good version remains in use."
        )
    return (
        ":warning: *Overall:* An automatic check needs attention. Existing "
        "safe data stays in place where possible, and urgent failures are sent "
        "as a separate plain-English alert."
    )


def today_lines(snapshot: dict) -> list[str]:
    lines = ["Live bus information and the public website are current."]
    timetable = _dict(snapshot.get("timetable_automation"))
    if timetable.get("status") == "failed":
        lines.append(
            "A proposed timetable update looked incomplete and was rejected. "
            "The current working timetable stayed live and the Pi will retry "
            "automatically."
        )
    data = _dict(snapshot.get("data_health"))
    summary = _dict(data.get("summary"))
    gaps = any(int(summary.get(key, 0) or 0) for key in (
        "missing_fleet", "missing_livery"))
    blurbs = _dict(summary.get("missing_blurbs"))
    gaps = gaps or any(int(value or 0) for value in blurbs.values())
    if gaps:
        lines.append(
            "Some buses still lack trustworthy source details or optional "
            "descriptions. Safe fallbacks are being used and the weekly Pi "
            "checks will keep looking for improvements."
        )
    anomalies = _dict(snapshot.get("collector_anomaly"))
    anomaly_status = str(anomalies.get("status") or "unavailable")
    if anomaly_status == "attention":
        lines.append(
            "The overnight checker found possible odd delay or matching "
            "readings to investigate. These are clues, not confirmed faults, "
            "and no public figures were changed."
        )
    elif anomaly_status in {"unavailable", "stale"}:
        lines.append(
            "The overnight odd-reading check is not current and needs attention."
        )
    else:
        lines.append("The overnight odd-reading check found nothing urgent.")
    return lines


def action_line(snapshot: dict) -> str:
    blurbs = _dict(snapshot.get("blurb_generation"))
    if blurbs.get("status") == "pending_review":
        pending = _dict(blurbs.get("pending_review"))
        buses = int(pending.get("buses", 0) or 0)
        noun = "bus" if buses == 1 else "buses"
        return (
            f"*You need to do:* Review descriptions for {buses} {noun} when "
            "convenient. Nothing will publish by itself."
        )
    issues = snapshot.get("issues")
    issues = issues if isinstance(issues, list) else []
    safe_update_issues = {
        "job:timetable-automation", "job:fleet-automation",
        "job:locality-automation", "job:blurb-generation",
        "job:editorial-refresh", "job:editorial-fetch",
        "job:editorial-promote",
    }
    if issues and set(map(str, issues)).issubset(safe_update_issues):
        return "*You need to do:* Nothing today. The Pi will retry automatically."
    if snapshot.get("status") != "ok":
        return (
            "*You need to do:* Nothing immediately unless a separate alert "
            "asks you for a decision; the safe version remains in use."
        )
    return "*You need to do:* Nothing today."


def daily_message(snapshot: dict, previous_state: dict | None = None,
                  today: date | None = None) -> str:
    heading, progress = progress_lines(snapshot, previous_state or {}, today)
    lines = [
        ":bus: *Bristol Bus Bot - daily update*",
        "",
        overall_line(snapshot),
        "",
        f"*{heading}*",
        *(f"- {line}" for line in progress),
        "",
        "*Where the plan stands*",
        "- The core automation checklist is complete; routine data updates no "
        "longer depend on the Windows PC.",
        "- The next real job is to save better evidence for suspicious readings "
        "and investigate genuine examples.",
        "- AI-written descriptions stay human-approved during the 30-day proving "
        "period.",
        "- Threads and new website ideas are optional and parked, not overdue "
        "core work.",
        "",
        "*Today's checks*",
        *(f"- {line}" for line in today_lines(snapshot)),
        "",
        action_line(snapshot),
    ]
    return "\n".join(lines)


def _write_state(snapshot: dict) -> None:
    payload = {
        "schema_version": 1,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "fingerprints": _progress_fingerprints(snapshot),
    }
    DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DAILY_STATE.with_name(DAILY_STATE.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, DAILY_STATE)


def main() -> None:
    snapshot = _read_json(AGGREGATE_HEALTH)
    try:
        previous = _read_json(DAILY_STATE)
    except (OSError, json.JSONDecodeError, ValueError):
        previous = {}
    message = daily_message(snapshot, previous)
    if _post(message):
        _write_state(snapshot)
        print("daily update posted")


if __name__ == "__main__":
    main()
