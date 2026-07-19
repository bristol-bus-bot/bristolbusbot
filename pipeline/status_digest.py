#!/usr/bin/env python3
"""Twice-daily estate digest sent to Slack by the systemd timer.

One message, five sections:
  collector  - freshest vehicle age, active count, match rate
  matching   - vehicles matched to a timetable trip
               whose delay is NULL (every reading refused by the distance
               gates) — the signature of a wrong-schedule match. Rising
               number = the matcher is pairing buses with wrong schedules.
  bot        - successful Bluesky posts today from durable delivery records
  site       - production site /healthz on :5002
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
    lines += [bot_line(), site_line(), pi_line()]
    _post("\n".join(lines))
    print("digest posted")


if __name__ == "__main__":
    main()
