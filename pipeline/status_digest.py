#!/usr/bin/env python3
"""Twice-daily estate digest sent to Slack by the systemd timer.

One message with these sections:
  collector  - freshest vehicle age, active count, match rate
  matching   - vehicles matched to a timetable trip
               whose delay is NULL (every reading refused by the distance
               gates) — the signature of a wrong-schedule match. Rising
               number = the matcher is pairing buses with wrong schedules.
  bot        - successful Bluesky posts today from durable delivery records
  site       - production site /healthz on :5002
  timetable  - aggregate last accepted and last attempted automation state
  fleet      - latest guarded weekly fleet-data refresh outcome
  blurbs     - pending human review and bounded Gemini usage
  data       - report-only enrichment completeness findings
  social     - Slack curation rollout mode and durable card deliveries
  pi         - disk, memory, CPU temperature

Webhook read directly from ~/.config/busbot-alerts/webhook (never assume
helper paths). Every section is best-effort: a broken probe reports itself
rather than killing the digest.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta
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


def _post(text: str) -> None:
    if not WEBHOOK_CONF.exists():
        print("no webhook config; printing instead:\n" + text)
        return
    url = WEBHOOK_CONF.read_text().strip().splitlines()[0].strip()
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


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
        missing_livery = int(summary.get("missing_livery", 0))
        missing_stops = int(summary.get("missing_stop_localities", 0))
        blurbs = summary.get("missing_blurbs")
        blurbs = blurbs if isinstance(blurbs, dict) else {}
        missing_blurbs = max((int(value) for value in blurbs.values()), default=0)
        return (
            f"*data*  :warning: {missing_fleet} without fleet data - "
            f"{missing_livery} without livery - {missing_blurbs} without blurbs - "
            f"{missing_stops} stops without locality - report-only"
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


def main() -> None:
    stamp = datetime.now().strftime("%a %H:%M")
    lines = [f":bus: *estate digest* — {stamp}"]
    lines += collector_lines()
    lines += [bot_line(), site_line(), timetable_line(), fleet_line(),
              data_health_line(), blurb_line(), social_line(), pi_line()]
    _post("\n".join(lines))
    print("digest posted")


if __name__ == "__main__":
    main()
