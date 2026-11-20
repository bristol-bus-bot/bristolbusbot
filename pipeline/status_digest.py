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
# Tom's phone (ntfy). Slack is the full update; the phone gets one line.
PHONE_CONF = HOME / ".config" / "busbot-alerts" / "ntfy"
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
DESCRIPTION_HISTORY = Path(os.getenv(
    "BBB_BLURB_HISTORY", "/var/lib/bristolbusbot/blurb-pending/history"))
EDITORIAL_CONTEXT = Path(os.getenv(
    "BBB_EDITORIAL_CONTEXT",
    "/var/lib/bristolbusbot-editorial/editorial-context.json"))


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


ATTENTION = ("Bus bot: something needs your attention", "Details are in Slack.")


def phone_summary(snapshot: dict, today: date | None = None) -> tuple[str, str, int]:
    """(title, message, ntfy priority) for the daily phone note.

    Anyone who knows the topic can read it, so it only ever says the state;
    the details are in the Slack update. All fine is quiet."""
    if recovery_status(snapshot, today)[0]:
        return (*ATTENTION, 4)
    blurbs = _dict(snapshot.get("blurb_generation"))
    if blurbs.get("status") == "pending_review" or snapshot.get("status") == "warning":
        return (*ATTENTION, 3)
    issues = [str(issue) for issue in (snapshot.get("issues") or [])
              if isinstance(snapshot.get("issues"), list)]
    if snapshot.get("status") == "ok" and not issues:
        return ("Bus bot: all fine", "Everything is working.", 2)
    return ("Bus bot: a snag, nothing to do yet", "Details are in Slack.", 3)


def _post_phone(title: str, message: str, priority: int) -> bool:
    try:
        url = PHONE_CONF.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    topic = url.removeprefix("https://ntfy.sh/").strip("/")
    if not url.startswith("https://ntfy.sh/") \
            or not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", topic):
        return False
    request = urllib.request.Request(
        "https://ntfy.sh/", data=json.dumps({
            "topic": topic, "title": title, "message": message,
            "priority": priority, "tags": ["bus"],
        }).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=15).read()
        return True
    except OSError:
        return False


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
        return f"*site*  :white_check_mark: healthz {r.status}: {body.strip()[:80]}"
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


def _count(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _day(value: object) -> str | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return f"{moment.day} {moment.strftime('%b')}"


def _days_since(value: object, today: date) -> int | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None
    return max(0, (today - moment).days)


def standing_counts(snapshot: dict) -> dict[str, int | None]:
    """The counts behind the two standing notes, for day-to-day comparison."""
    summary = _dict(_dict(snapshot.get("data_health")).get("summary"))
    missing = (_count(summary.get("missing_fleet")) + _count(summary.get("missing_livery"))
               + sum(_count(value) for value in _dict(summary.get("missing_blurbs")).values()))
    anomalies = _dict(snapshot.get("collector_anomaly"))
    odd = None
    if str(anomalies.get("status") or "unavailable") not in {"unavailable", "stale"}:
        odd = sum(_count(_dict(item).get("count"))
                  for item in _dict(anomalies.get("detectors")).values())
    return {"missing_details": missing if summary else None, "odd_readings": odd}


def timetable_days_failing(snapshot: dict, today: date) -> int | None:
    return timetable_failing_days(snapshot, today)


TIMETABLE_MAX_AGE_DAYS = 7


def problems(snapshot: dict, today: date | None = None) -> list[str]:
    """Sentences for what is wrong today, each naming the thing and why."""
    today = today or datetime.now(timezone.utc).date()
    found: list[str] = [str(item) for item in snapshot.get("failures") or []
                        if isinstance(snapshot.get("failures"), list)]
    timetable = _dict(snapshot.get("timetable_automation"))
    accepted = _dict(timetable.get("last_accepted"))
    if timetable.get("status") == "failed":
        stuck = timetable_failing_days(snapshot, today) or 0
        what = str(snapshot.get("timetable_problem") or
                   "It failed a safety check that has no plain description yet.")
        if stuck >= TIMETABLE_STUCK_DAYS:
            found.append(f"Timetable updates have been rejected for {stuck} days running. "
                         f"{what} The current timetable stays live, but retries are not fixing it.")
    size = _dict(snapshot.get("timetable_size"))
    if _count(size.get("limit_bytes")) and \
            _count(size.get("bytes")) >= TIMETABLE_SIZE_WARNING * _count(size.get("limit_bytes")):
        found.append(f"The timetable file is {_count(size.get('bytes')) / MIB:.0f} MiB, over 90% of "
                     f"the {_count(size.get('limit_bytes')) / MIB:.0f} MiB the Pi accepts; "
                     "the next update may be refused.")
    age = _days_since(accepted.get("accepted_at"), today)
    if age is None:
        found.append("The timetable's last update date is unknown.")
    elif age > TIMETABLE_MAX_AGE_DAYS:
        found.append(f"The timetable was last updated {age} days ago "
                     f"({_day(accepted.get('accepted_at'))}), longer than the usual week.")
    anomalies = _dict(snapshot.get("collector_anomaly"))
    if str(anomalies.get("status") or "unavailable") in {"unavailable", "stale"}:
        found.append("The overnight odd-reading check did not produce a current report.")
    return found


def recovery_status(snapshot: dict, today: date | None = None) -> tuple[list[str], list[str]]:
    """Kept for the phone note: (problems, nothing)."""
    return problems(snapshot, today), []


def overall_line(snapshot: dict, found: list[str] | None = None) -> str:
    issues = snapshot.get("issues")
    issues = issues if isinstance(issues, list) else []
    if found:
        return ":warning: *Something needs looking at.*"
    timetable = _dict(snapshot.get("timetable_automation"))
    if timetable.get("status") == "failed":
        return (":white_check_mark: *Everything is working.* A proposed timetable "
                "update was rejected safely; the current one stays live.")
    if snapshot.get("status") == "ok" and not issues:
        return ":white_check_mark: *Everything is working.*"
    areas = _dict(snapshot.get("issue_areas"))
    named = sorted({str(areas.get(issue) or "an automatic check") for issue in issues})
    if snapshot.get("status") == "warning":
        return (":large_yellow_circle: *Everything is working.* Coming up soon: "
                + ", ".join(named) + ".")
    return ":warning: *Something needs looking at:* " + ", ".join(named) + "."


def timetable_today_line(snapshot: dict, today: date) -> str | None:
    """Only on a day the timetable is not otherwise mentioned."""
    timetable = _dict(snapshot.get("timetable_automation"))
    accepted = _dict(timetable.get("last_accepted"))
    if timetable.get("status") == "failed":
        what = str(snapshot.get("timetable_problem") or "")
        return ("A proposed timetable update was rejected. " + (what + " " if what else "")
                + "The current timetable stays live and the Pi will retry at the next check.")
    when = _day(accepted.get("accepted_at"))
    age = _days_since(accepted.get("accepted_at"), today)
    if when is None or age is None or age > TIMETABLE_MAX_AGE_DAYS:
        return None    # reported as a problem instead
    return f"Timetable: current, last updated {when}."


TIMETABLE_SIZE_WARNING = 0.9
MIB = 1024 * 1024


def timetable_size_lines(snapshot: dict, previous_state: dict, today: date) -> list[str]:
    """Mondays: the timetable's size and its change over the week (#130)."""
    size = _dict(snapshot.get("timetable_size"))
    current, limit = _count(size.get("bytes")), _count(size.get("limit_bytes"))
    if not current or not limit or today.weekday() != 0:
        return []
    history = _dict(previous_state.get("timetable_bytes"))
    week_ago = (today - timedelta(days=7)).isoformat()
    earlier = [value for day, value in sorted(history.items()) if day <= week_ago]
    line = (f"Timetable file: {current / MIB:.0f} MiB, {100 * current / limit:.0f}% of the "
            f"{limit / MIB:.0f} MiB the Pi accepts")
    if earlier:
        change = (current - _count(earlier[-1])) / MIB
        line += f" ({'up' if change >= 0 else 'down'} {abs(change):.0f} MiB on last week)"
    return [line + "."]


def change_lines(snapshot: dict, previous_state: dict) -> list[str]:
    """Standing notes only when their count changed since yesterday."""
    before = _dict(previous_state.get("counts"))
    now = standing_counts(snapshot)
    lines = []
    labels = {
        "missing_details": "Buses still missing source details or descriptions",
        "odd_readings": "Odd readings for Claude to look at (clues, not faults)",
    }
    for key, label in labels.items():
        current, earlier = now.get(key), before.get(key)
        if current is None or current == earlier:
            continue
        if earlier is None:
            lines.append(f"{label}: {current}.")
        else:
            direction = "up" if current > earlier else "down"
            lines.append(f"{label}: {current}, {direction} from {earlier}.")
    return lines


def timetable_failing_days(snapshot: dict, today: date | None = None) -> int | None:
    """Days since the timetable check last succeeded, while it is failing."""
    timetable = _dict(snapshot.get('timetable_automation'))
    if timetable.get('status') != 'failed':
        return None
    today = today or datetime.now(timezone.utc).date()
    job = _dict(_dict(timetable.get('delivery')).get('job'))
    days = []
    # A routine "nothing due yet" skip also means the check was working.
    for key in ('last_success_at', 'last_skipped_at', 'last_ok_at'):
        try:
            days.append(max(0, (today - datetime.fromisoformat(
                str(job.get(key)).replace('Z', '+00:00')).date()).days))
        except (ValueError, TypeError):
            continue
    return min(days) if days else None  # unknown is reported separately


TIMETABLE_STUCK_DAYS = 2


def moving_occasion_lines(today: date | None = None,
                          path: Path | None = None) -> list[str]:
    """Moving-date occasions (Easter, Diwali...) are one entry per year. When
    the latest entry for one ended over 30 days ago, next year's is missing."""
    today = today or date.today()
    try:
        document = json.loads((path or EDITORIAL_CONTEXT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    latest: dict[str, tuple[date, str]] = {}
    for item in document.get("occasions", []) if isinstance(document, dict) else []:
        schedule = _dict(_dict(item).get("schedule"))
        if schedule.get("kind") != "date_range":
            continue
        name = re.sub(r"-\d{4}$", "", str(item.get("id", "")))
        try:
            end = date.fromisoformat(str(schedule.get("end")))
        except ValueError:
            continue
        if name not in latest or end > latest[name][0]:
            latest[name] = (end, str(item.get("label") or name))
    overdue = sorted(label for end, label in latest.values() if (today - end).days > 30)
    if not overdue:
        return []
    return [f"Occasions needing next year's dates: {', '.join(overdue)}. "
            "Ask Claude to add them, checked against a source."]


def published_description_lines(today: date | None = None,
                                history: Path | None = None,
                                since: datetime | None = None) -> list[str]:
    """One line: descriptions published in the last week. The 'pull any you
    don't like' prompt only when some are new since the previous message."""
    today = today or datetime.now(timezone.utc).date()
    count, new = 0, 0
    for path in sorted((history or DESCRIPTION_HISTORY).glob("*.approved.json")):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(
                str(batch.get("created_at", "")).replace("Z", "+00:00"))
        except (OSError, ValueError):
            continue
        if not 0 <= (today - created.date()).days <= 7:
            continue
        items = sum(len(_dict(entries)) for entries in _dict(batch.get("additions")).values())
        count += items
        if since is not None and created.tzinfo is not None and created > since:
            new += items
    if not new:
        return []
    noun = "description" if new == 1 else "descriptions"
    return [f"{new} new bus {noun} went live ({count} this week). They show on each "
            "bus's page on bristolbuses.live; ask Claude to pull any you don't like."]


def action_line(snapshot: dict, found: list[str]) -> str:
    if found:
        return "*You need to do:* Ask Claude to look at the problem above; automatic retries have not fixed it."
    blurbs = _dict(snapshot.get("blurb_generation"))
    if blurbs.get("status") == "pending_review":
        buses = int(_dict(blurbs.get("pending_review")).get("buses", 0) or 0)
        noun = "bus" if buses == 1 else "buses"
        return (f"*You need to do:* Review descriptions for {buses} {noun} when "
                "convenient. Nothing will publish by itself.")
    return "*You need to do:* nothing."


def daily_message(snapshot: dict, previous_state: dict | None = None,
                  today: date | None = None,
                  history: Path | None = None) -> str:
    """Only what is true today: no fixed text beyond the heading (#141)."""
    today = today or datetime.now(timezone.utc).date()
    previous_state = previous_state or {}
    found = problems(snapshot, today)
    heading, progress = progress_lines(snapshot, previous_state, today)
    try:
        since = datetime.fromisoformat(str(previous_state.get("sent_at")))
    except (TypeError, ValueError):
        since = None
    notes = [str(note) for note in snapshot.get("notes") or []
             if isinstance(snapshot.get("notes"), list)]
    timetable_note = timetable_today_line(snapshot, today)
    reviews = _dict(snapshot.get("editorial_reviews"))
    overdue_count = _count(reviews.get("count"))
    oldest_due = _day(reviews.get("oldest_due"))
    if any(line.startswith("Timetable updates have been rejected") for line in found):
        timetable_note = None    # already said under the problem
    today_items = [
        *([timetable_note] if timetable_note else []),
        *change_lines(snapshot, previous_state),
        *timetable_size_lines(snapshot, previous_state, today),
        *notes,
        *published_description_lines(today, history, since),
        *moving_occasion_lines(today),
        *([f"Facts due a check: {overdue_count} (oldest due {oldest_due})"]
          if overdue_count > 0 and oldest_due else []),
    ]
    lines = [":bus: *Bristol Bus Bot - daily update*", "", overall_line(snapshot, found)]
    if found:
        lines += ["", *(f"- {line}" for line in found)]
    lines += ["", f"*{heading}*", *(f"- {line}" for line in progress)]
    if today_items:
        lines += ["", "*Today*", *(f"- {line}" for line in today_items)]
    lines += ["", action_line(snapshot, found)]
    return "\n".join(lines)


def _size_history(snapshot: dict, previous: dict) -> dict[str, int]:
    """The last five weeks of daily timetable sizes, for the weekly change."""
    history = {day: _count(value) for day, value in _dict(previous.get("timetable_bytes")).items()}
    size = _count(_dict(snapshot.get("timetable_size")).get("bytes"))
    if size:
        history[datetime.now(timezone.utc).date().isoformat()] = size
    return dict(sorted(history.items())[-35:])


def _write_state(snapshot: dict, previous: dict | None = None) -> None:
    previous = previous or {}
    payload = {
        "schema_version": 2,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "fingerprints": _progress_fingerprints(snapshot),
        "counts": standing_counts(snapshot),
        "timetable_bytes": _size_history(snapshot, previous),
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
    if _post_phone(*phone_summary(snapshot)):
        print("daily phone summary sent")
    if _post(message):
        _write_state(snapshot, previous)
        print("daily update posted")


if __name__ == "__main__":
    main()
